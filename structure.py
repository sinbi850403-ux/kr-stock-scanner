"""
구조 감지 (순수) — 스윙 피봇 / 피보 황금구간 / 오더블록 / BOS·CHoCH 상태머신.

Pine 정합성:
  - 피봇: 좌우 piv_len 봉보다 strictly 높/낮음 (확정은 piv_len 봉 후행)
  - 황금구간: f500~f618 (= 0.5~0.618 되돌림) 밴드
  - 오더블록: 변위봉(close>open, |body|>atr*1.2, rvol>=surge, close>high[-1]) + 직전 음봉
              미티게이션: 이후 종가가 OB 하단 이탈 시 무효
  - BOS/CHoCH: 확정 피봇 돌파로 os 갱신 (+1 강세 / -1 약세), bearCHoCH=1→-1 전환
"""
from dataclasses import dataclass
from typing import Optional, List, Tuple

import indicators as ind
from config import Config


@dataclass
class StructureState:
    swing_high: Optional[float]
    swing_low: Optional[float]
    gz_top: Optional[float]
    gz_bot: Optional[float]
    in_golden_zone: bool
    ob_top: Optional[float]
    ob_bot: Optional[float]
    in_ob: bool
    os: int
    bear_choch: bool


def find_pivots(highs: List[float], lows: List[float], piv_len: int):
    """확정 피봇 리스트 반환: (ph, pl) 각각 [(idx, value), ...]."""
    n = len(highs)
    ph, pl = [], []
    for i in range(piv_len, n - piv_len):
        h, l = highs[i], lows[i]
        left_h, right_h = highs[i - piv_len:i], highs[i + 1:i + piv_len + 1]
        if all(h > x for x in left_h) and all(h > x for x in right_h):
            ph.append((i, h))
        left_l, right_l = lows[i - piv_len:i], lows[i + 1:i + piv_len + 1]
        if all(l < x for x in left_l) and all(l < x for x in right_l):
            pl.append((i, l))
    return ph, pl


def golden_zone(swing_high: float, swing_low: float):
    top, bot = max(swing_high, swing_low), min(swing_high, swing_low)
    rng = top - bot
    if rng <= 0:
        return None, None
    f500 = top - rng * 0.5
    f618 = top - rng * 0.618
    return max(f500, f618), min(f500, f618)


def in_zone(bar_high: float, bar_low: float, gz_top, gz_bot) -> bool:
    if gz_top is None or gz_bot is None:
        return False
    return bar_low <= gz_top and bar_high >= gz_bot


def active_bull_ob(df, cfg: Config):
    """미티게이션 안 된 가장 최근 불리시 OB의 (top, bot). 없으면 None."""
    n = len(df)
    if n < 2:
        return None
    atr_s = ind.atr(df["high"], df["low"], df["close"], cfg.atr_period).tolist()
    rvol_s = ind.rvol(df["volume"], cfg.rvol_window).tolist()
    o = df["open"].tolist()
    c = df["close"].tolist()
    h = df["high"].tolist()
    l = df["low"].tolist()

    obs: List[Tuple[int, float, float]] = []
    for i in range(1, n):
        body = abs(c[i] - o[i])
        strong_up = (c[i] > o[i] and body > atr_s[i] * 1.2
                     and rvol_s[i] >= cfg.vol_surge and c[i] > h[i - 1])
        prior_bear = c[i - 1] < o[i - 1]
        if strong_up and prior_bear:
            obs.append((i, h[i - 1], l[i - 1]))

    for idx, top, bot in reversed(obs):
        broken = any(c[j] < bot for j in range(idx + 1, n))
        if not broken:
            return (top, bot)
    return None


def structure_os(highs: List[float], lows: List[float], closes: List[float], piv_len: int):
    """BOS/CHoCH 상태 (os, bear_choch) 순차 재생."""
    n = len(closes)
    ph, pl = find_pivots(highs, lows, piv_len)
    ph_conf = [(i + piv_len, v) for i, v in ph]   # 확정(사용가능) 시점
    pl_conf = [(i + piv_len, v) for i, v in pl]

    os = 0
    bos_up = bos_dn = None
    pi = li = 0
    bear_choch = False
    for t in range(n):
        while pi < len(ph_conf) and ph_conf[pi][0] <= t:
            bos_up = ph_conf[pi][1]
            pi += 1
        while li < len(pl_conf) and pl_conf[li][0] <= t:
            bos_dn = pl_conf[li][1]
            li += 1
        if t == 0:
            continue
        os_prev = os
        if bos_up is not None and closes[t] > bos_up and closes[t - 1] <= bos_up:
            os = 1
        if bos_dn is not None and closes[t] < bos_dn and closes[t - 1] >= bos_dn:
            os = -1
        if t == n - 1:
            bear_choch = (os_prev == 1 and os == -1)
    return os, bear_choch


def analyze(df, cfg: Config) -> StructureState:
    highs = df["high"].tolist()
    lows = df["low"].tolist()
    closes = df["close"].tolist()

    ph, pl = find_pivots(highs, lows, cfg.piv_len)
    sh = ph[-1][1] if ph else None
    sl = pl[-1][1] if pl else None

    gz_top = gz_bot = None
    in_gz = False
    if sh is not None and sl is not None:
        gz_top, gz_bot = golden_zone(sh, sl)
        in_gz = in_zone(highs[-1], lows[-1], gz_top, gz_bot)

    ob = active_bull_ob(df, cfg)
    ob_top = ob_bot = None
    in_ob = False
    if ob:
        ob_top, ob_bot = ob
        price = closes[-1]
        in_ob = ob_bot <= price <= ob_top

    os, choch = structure_os(highs, lows, closes, cfg.piv_len)
    return StructureState(sh, sl, gz_top, gz_bot, in_gz, ob_top, ob_bot, in_ob, os, choch)
