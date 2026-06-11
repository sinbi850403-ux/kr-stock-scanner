"""
신호 생성 (순수) — 컨플루언스 → Signal, 역신호 청산 판정.

진입: score >= cfg.threshold AND 유효 손절(sl<entry) AND 과열 아님
      (과열 = 종가가 EMA20 대비 max_ext_pct% 초과 이격 — 이미 분출한 종목 추격 차단).
청산(역신호): bearCHoCH / EMA 역배열 / 주봉 추세 이탈.
"""
from dataclasses import dataclass, field
from typing import Optional, Tuple

import indicators as ind
import confluence as cf
import risk
from config import Config


@dataclass
class Signal:
    symbol: str
    direction: str
    entry_price: float
    sl_price: float
    score: int
    layers: dict
    atr: float
    rvol: float
    prev_close: Optional[float] = None
    ob_bot: Optional[float] = None
    timestamp: str = ""


def generate_signal(symbol, df_d, df_w, df_m, cfg: Config,
                    prev_close: Optional[float] = None,
                    timestamp: str = "") -> Optional[Signal]:
    res = cf.full(df_d, df_w, df_m, cfg)
    if res is None or res.score < cfg.threshold:
        return None

    if res.ext_pct > cfg.max_ext_pct:   # 과열 — 추격 진입 차단
        return None

    sl = risk.compute_stop(res.price, res.atr, cfg,
                           ob_bot=res.ob_bot, prev_close=prev_close)
    if sl >= res.price:
        return None

    layers = {"L1": res.l1, "L2": res.l2, "L3": res.l3,
              "L4": res.l4, "L5": res.l5, "L6": res.l6}
    return Signal(symbol=symbol, direction="long",
                  entry_price=res.price, sl_price=sl, score=res.score,
                  layers=layers, atr=res.atr, rvol=res.rvol,
                  prev_close=prev_close, ob_bot=res.ob_bot, timestamp=timestamp)


def daily_direction(df_d, cfg: Config) -> int:
    """일봉 EMA 스택 방향. +1 상승 / -1 하락 / 0 불명확."""
    c = df_d["close"]
    if len(c) < cfg.ema_slow:
        return 0
    e5 = ind.ema(c, cfg.ema_fast).iloc[-1]
    e20 = ind.ema(c, cfg.ema_mid).iloc[-1]
    e60 = ind.ema(c, cfg.ema_slow).iloc[-1]
    if e5 > e20 > e60:
        return 1
    if e5 < e20 < e60:
        return -1
    return 0


def counter_exit(df_d, df_w, df_m, cfg: Config) -> Tuple[bool, str]:
    """보유 롱의 청산 판정 (True면 청산)."""
    res = cf.full(df_d, df_w, df_m, cfg)
    if res is None:
        return False, "데이터 부족"

    if res.bear_choch:
        return True, "CHoCH 약세 전환"

    c = df_d["close"]
    e5 = ind.ema(c, cfg.ema_fast).iloc[-1]
    e20 = ind.ema(c, cfg.ema_mid).iloc[-1]
    e60 = ind.ema(c, cfg.ema_slow).iloc[-1]
    if e5 < e20 < e60:
        return True, "EMA 역배열"

    w_pair = cf._mtf_pair(df_w, cfg)
    if w_pair is not None and w_pair[0] < w_pair[1]:
        return True, "상위TF(주봉) 추세 이탈"

    return False, "추세 유지"
