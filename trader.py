"""
자동주문 엔진 — 진입(시장가 매수 + TP 지정가 3분할) / TP 체결 감지 / 추적 SL /
역신호·SL 청산.

OCO 미지원: TP는 거래소 상주 지정가, SL은 소프트웨어 감시(현재가 폴링) 후 시장가 청산.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import pytz

import risk
from config import Config

log = logging.getLogger(__name__)
KST = pytz.timezone("Asia/Seoul")


@dataclass
class EntryInfo:
    symbol: str
    name: str
    side: str
    entry_price: float
    entry_qty: int
    holding_qty: int
    sl_price: float
    tp1_price: float
    tp2_price: float
    tp3_price: float
    qty1: int
    qty2: int
    qty3: int
    risk_r: float
    tp_count: int = 0
    order_ids: dict = field(default_factory=dict)
    entry_time: str = ""


class AutoTrader:
    def __init__(self, cfg: Config, client, notifier):
        self.cfg = cfg
        self.client = client
        self.notifier = notifier

    # ── 진입 ──
    def execute_signal(self, signal, name, now=None) -> Optional[EntryInfo]:
        cash, _ = self.client.get_balance()
        params = risk.calc_trade(signal.entry_price, signal.sl_price, cash, self.cfg)
        if params is None:
            log.warning("사이징 실패 %s — 진입 취소", signal.symbol)
            return None

        buy_id = self.client.place_buy_order(signal.symbol, params.total_qty, price=None)
        if not buy_id:
            log.error("매수 주문 실패 %s — 진입 취소", signal.symbol)
            return None

        order_ids = {"entry": buy_id}
        for i, (qty, price) in enumerate(
                [(params.qty1, params.tp1_price),
                 (params.qty2, params.tp2_price),
                 (params.qty3, params.tp3_price)], start=1):
            if qty > 0:
                order_ids[f"tp{i}"] = self.client.place_sell_order(signal.symbol, qty, price)

        now = now or datetime.now(KST)
        ei = EntryInfo(
            symbol=signal.symbol, name=name, side="long",
            entry_price=params.entry_price, entry_qty=params.total_qty,
            holding_qty=params.total_qty, sl_price=params.sl_price,
            tp1_price=params.tp1_price, tp2_price=params.tp2_price,
            tp3_price=params.tp3_price, qty1=params.qty1, qty2=params.qty2,
            qty3=params.qty3, risk_r=params.risk_r, tp_count=0,
            order_ids=order_ids, entry_time=now.strftime("%Y-%m-%d %H:%M:%S"))
        self.notifier.alert_entry(signal.symbol, name, params)
        return ei

    # ── TP 체결 감지 ──
    def _tp_count_from_qty(self, ei: EntryInfo, cur_qty: int) -> int:
        if cur_qty <= 0:
            return 3
        filled = ei.entry_qty - cur_qty
        if filled >= ei.qty1 + ei.qty2:
            return 2
        if filled >= ei.qty1:
            return 1
        return 0

    def on_fill_check(self, ei: EntryInfo) -> Optional[EntryInfo]:
        pos = self.client.get_position(ei.symbol)
        cur_qty = pos["qty"] if pos else 0
        if cur_qty <= 0:
            return None  # 완전 청산 (TP3 또는 전량체결)

        if cur_qty < ei.holding_qty:
            new_count = self._tp_count_from_qty(ei, cur_qty)
            filled_now = ei.holding_qty - cur_qty
            ei.holding_qty = cur_qty
            if new_count > ei.tp_count:
                ei.tp_count = new_count
                ei.sl_price = risk.trailing_sl(ei.entry_price, ei.sl_price,
                                               ei.risk_r, ei.tp_count, self.cfg)
                tp_price = {1: ei.tp1_price, 2: ei.tp2_price}.get(new_count, ei.tp3_price)
                pnl = (tp_price - ei.entry_price) * filled_now
                self.notifier.alert_tp(new_count, ei.symbol, filled_now, tp_price, pnl)
        return ei

    # ── 청산 ──
    def close_all(self, ei: EntryInfo, reason: str, price=None, reason_text=""):
        for k, qty in (("tp1", ei.qty1), ("tp2", ei.qty2), ("tp3", ei.qty3)):
            oid = ei.order_ids.get(k)
            if oid:
                self.client.cancel_order(oid, ei.symbol, qty)
        if ei.holding_qty > 0:
            self.client.place_sell_order(ei.symbol, ei.holding_qty, price=None)  # 시장가
        exit_px = price if price is not None else ei.sl_price
        pnl = (exit_px - ei.entry_price) * ei.holding_qty
        if reason == "SL":
            self.notifier.alert_sl(ei.symbol, ei.entry_price, ei.sl_price, pnl)
        else:
            self.notifier.alert_counter(ei.symbol, ei.entry_price, exit_px, reason_text or reason)

    # ── 사이클 감시 ──
    def monitor(self, ei: EntryInfo, current_price: float,
                counter=False, counter_reason="") -> Optional[EntryInfo]:
        if counter:
            self.close_all(ei, "counter", price=current_price, reason_text=counter_reason)
            return None
        ei = self.on_fill_check(ei)
        if ei is None:
            return None
        if current_price <= ei.sl_price:
            self.close_all(ei, "SL", price=current_price)
            return None
        return ei
