"""
6레이어 컨플루언스 점수 (순수) — 일/주/월봉 기반.

L1 3EMA 정배열   : e5>e20>e60 AND close>e60 (일봉)
L2 실제 MTF      : 일(e20>e60 & macd>0) AND 주(e20>e60) AND 월(e20>e60)
L3 황금구간/OB   : 피보 황금구간(0.5~0.618) OR 활성 불리시 OB 내부
L4 모멘텀        : RSI 45 상향돌파 OR (MACD 골든크로스 & macd>0 & hist↑)
L5 거래량 3증거  : (rvol≥1.7)+(OBV↑)+(close>VWAP) ≥ 2 AND not 분배 AND 양봉
L6 구조 상태머신 : os == 1 (BOS 강세 확정)
"""
import math
from dataclasses import dataclass
from typing import Optional

import indicators as ind
import structure as st
from config import Config


@dataclass
class ConfluenceResult:
    score: int
    l1: bool
    l2: bool
    l3: bool
    l4: bool
    l5: bool
    l6: bool
    price: float
    atr: float
    rvol: float
    bear_choch: bool
    ob_bot: Optional[float]
    state: st.StructureState
    ext_pct: float = 0.0     # 종가의 EMA20 대비 이격률(%) — 과열 판정용


# ── 레이어 순수 헬퍼 ────────────────────────────────────
def l1_trend(e5, e20, e60, close) -> bool:
    return bool(e5 > e20 and e20 > e60 and close > e60)


def l2_mtf(d, w, m) -> bool:
    """d=(e20,e60,macd), w=(e20,e60), m=(e20,e60)."""
    return bool(d[0] > d[1] and d[2] > 0 and w[0] > w[1] and m[0] > m[1])


def l4_momentum(rsi_prev, rsi_now, rsi_buy,
                macd_prev, macd_now, sig_prev, sig_now,
                hist_now, hist_prev) -> bool:
    rsi_x = rsi_prev < rsi_buy <= rsi_now
    macd_x = (macd_prev < sig_prev and macd_now >= sig_now
              and macd_now > 0 and hist_now > hist_prev)
    return bool(rsi_x or macd_x)


def is_distribution(rvol_now, vol_strong, body, bar_range, close, hi10) -> bool:
    small_body = (body < bar_range * 0.2) if bar_range > 0 else False
    return bool(rvol_now >= vol_strong and small_body and close >= hi10 * 0.99)


def l5_volume(rvol_now, obv_now, obv_prev, close, vwap, open_, vol_surge,
              distribution) -> bool:
    cnt = ((1 if rvol_now >= vol_surge else 0)
           + (1 if obv_now > obv_prev else 0)
           + (1 if close > vwap else 0))
    return bool(cnt >= 2 and not distribution and close >= open_)


def _mtf_pair(df, cfg: Config):
    """주/월봉 (e20, e60) 반환. 데이터 부족 시 None (보수적: L2 불충족)."""
    if df is None or len(df) < cfg.ema_mid:
        return None
    c = df["close"]
    e20 = ind.ema(c, cfg.ema_mid).iloc[-1]
    e60 = ind.ema(c, cfg.ema_slow).iloc[-1]
    return (e20, e60)


def _f(x, default=0.0):
    try:
        v = float(x)
        return default if math.isnan(v) else v
    except (TypeError, ValueError):
        return default


def full(df_d, df_w, df_m, cfg: Config) -> Optional[ConfluenceResult]:
    if df_d is None or len(df_d) < cfg.ema_slow + 5:
        return None

    c, h, l, o, v = (df_d["close"], df_d["high"], df_d["low"],
                     df_d["open"], df_d["volume"])
    e5 = ind.ema(c, cfg.ema_fast)
    e20 = ind.ema(c, cfg.ema_mid)
    e60 = ind.ema(c, cfg.ema_slow)
    rsi = ind.rsi(c, cfg.rsi_period)
    macd_line, sig, hist = ind.macd(c, cfg.macd_fast, cfg.macd_slow, cfg.macd_signal)
    atr_s = ind.atr(h, l, c, cfg.atr_period)
    rvol_s = ind.rvol(v, cfg.rvol_window)
    obv_s = ind.obv(c, v)
    vwap_s = ind.vwap_rolling(h, l, c, v, cfg.vwap_window)

    price = _f(c.iloc[-1])
    cur_atr = _f(atr_s.iloc[-1])
    cur_rvol = _f(rvol_s.iloc[-1])
    open_now = _f(o.iloc[-1])

    # L1
    L1 = l1_trend(_f(e5.iloc[-1]), _f(e20.iloc[-1]), _f(e60.iloc[-1]), price)

    # L2
    d_tuple = (_f(e20.iloc[-1]), _f(e60.iloc[-1]), _f(macd_line.iloc[-1]))
    w_pair = _mtf_pair(df_w, cfg)
    m_pair = _mtf_pair(df_m, cfg)
    L2 = False if (w_pair is None or m_pair is None) else l2_mtf(d_tuple, w_pair, m_pair)

    # L3 (구조)
    state = st.analyze(df_d, cfg)
    L3 = bool(state.in_golden_zone or state.in_ob)

    # L4
    L4 = l4_momentum(_f(rsi.iloc[-2]), _f(rsi.iloc[-1]), cfg.rsi_buy,
                     _f(macd_line.iloc[-2]), _f(macd_line.iloc[-1]),
                     _f(sig.iloc[-2]), _f(sig.iloc[-1]),
                     _f(hist.iloc[-1]), _f(hist.iloc[-2]))

    # L5
    body = abs(price - open_now)
    bar_range = _f(h.iloc[-1]) - _f(l.iloc[-1])
    hi10 = _f(h.iloc[-10:].max())
    dist = is_distribution(cur_rvol, cfg.vol_strong, body, bar_range, price, hi10)
    L5 = l5_volume(cur_rvol, _f(obv_s.iloc[-1]), _f(obv_s.iloc[-2]),
                   price, _f(vwap_s.iloc[-1]), open_now, cfg.vol_surge, dist)

    # L6
    L6 = bool(state.os == 1)

    score = int(L1) + int(L2) + int(L3) + int(L4) + int(L5) + int(L6)

    e20_now = _f(e20.iloc[-1])
    ext_pct = (price - e20_now) / e20_now * 100.0 if e20_now > 0 else 0.0

    return ConfluenceResult(score, L1, L2, L3, L4, L5, L6,
                            price, cur_atr, cur_rvol, state.bear_choch,
                            state.ob_bot, state, ext_pct=ext_pct)
