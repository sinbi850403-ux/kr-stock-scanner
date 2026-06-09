"""
리스크 관리 (순수) — 호가단위·포지션사이징·3분할·손절계산·추적SL.

호가단위: KRX 2023 개편 기준 (검증필요 — docs/DESIGN.md §10).
"""
import math
from dataclasses import dataclass, field
from typing import Optional

from config import Config


@dataclass
class TradeParams:
    entry_price: float
    sl_price: float
    tp1_price: float
    tp2_price: float
    tp3_price: float
    total_qty: int
    qty1: int
    qty2: int
    qty3: int
    risk_krw: float
    risk_r: float
    side: str = "long"
    order_ids: dict = field(default_factory=dict)


# KRX 호가단위 (가격상한, 틱) — 2023.1 개편
_TICKS = [
    (2_000, 1),
    (5_000, 5),
    (20_000, 10),
    (50_000, 50),
    (200_000, 100),
    (500_000, 500),
]
_TICK_TOP = 1_000


def tick_size(price: float) -> int:
    for ceiling, tick in _TICKS:
        if price < ceiling:
            return tick
    return _TICK_TOP


def round_price_to_tick(price: float) -> int:
    tick = tick_size(price)
    return int(math.floor(price / tick + 0.5)) * tick


def compute_stop(entry: float, atr: float, cfg: Config,
                 ob_bot: Optional[float] = None,
                 prev_close: Optional[float] = None) -> float:
    """ATR 손절과 OB 하단 중 더 가까운(낮은) 쪽, 하한가로 클램프."""
    atr_stop = entry - atr * cfg.sl_atr_mult
    stop = atr_stop if ob_bot is None else min(atr_stop, ob_bot)
    if prev_close:
        limit_down = prev_close * (1 - cfg.price_limit_pct / 100.0)
        stop = max(stop, limit_down)
    return stop


def calc_trade(entry: float, sl: float, balance: float, cfg: Config) -> Optional[TradeParams]:
    risk_distance = entry - sl
    if risk_distance <= 0:
        return None

    risk_krw = balance * cfg.risk_pct
    qty = int(math.floor(risk_krw / risk_distance))
    qty = min(qty, int(math.floor(balance / entry)))
    if qty < 1:
        return None

    qty1 = qty // 3
    qty2 = qty // 3
    qty3 = qty - qty1 - qty2

    r = risk_distance
    return TradeParams(
        entry_price=round_price_to_tick(entry),
        sl_price=round_price_to_tick(sl),
        tp1_price=round_price_to_tick(entry + r * cfg.tp1_r),
        tp2_price=round_price_to_tick(entry + r * cfg.tp2_r),
        tp3_price=round_price_to_tick(entry + r * cfg.tp3_r),
        total_qty=qty,
        qty1=qty1, qty2=qty2, qty3=qty3,
        risk_krw=risk_krw,
        risk_r=r,
    )


def trailing_sl(entry: float, initial_sl: float, risk_r: float,
                tp_count: int, cfg: Config) -> int:
    """tp_count 기반 추적 SL. 0=초기 / 1=본전+0.5R / 2=이익+1.5R."""
    if tp_count <= 0:
        return round_price_to_tick(initial_sl)
    if tp_count == 1:
        return round_price_to_tick(entry + 0.5 * risk_r)
    return round_price_to_tick(entry + 1.5 * risk_r)
