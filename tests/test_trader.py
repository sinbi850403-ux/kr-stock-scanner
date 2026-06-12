"""trader.py 테스트 — 진입/TP감지/추적SL/청산 (mock client·notifier)."""
import pytest
from config import Config
import trader as tr
from strategy import Signal


def _cfg(**kw):
    base = dict(app_key="k", app_secret="s", cano="12345678",
                telegram_token="t", telegram_chat="c")
    base.update(kw)
    return Config(**base)


class MockClient:
    def __init__(self, cash=10_000_000, position=None):
        self.cash = cash
        self._position = position
        self.buys, self.sells, self.cancels = [], [], []
        self._sid = 0

    def get_balance(self):
        return self.cash, ([self._position] if self._position else [])

    def get_position(self, symbol):
        return self._position

    def set_position_qty(self, qty):
        self._position = None if qty <= 0 else {"symbol": "005930", "qty": qty,
                                                "avg": 70200, "price": 70200}

    def place_buy_order(self, symbol, qty, price=None):
        self.buys.append((symbol, qty, price))
        return "BUY1"

    def place_sell_order(self, symbol, qty, price=None):
        self.sells.append((symbol, qty, price))
        self._sid += 1
        return f"S{self._sid}"

    def cancel_order(self, order_id, symbol, qty, price=0):
        self.cancels.append(order_id)
        return {}


class MockNotifier:
    def __init__(self):
        self.calls = []

    def alert_entry(self, *a):
        self.calls.append(("entry",) + a)

    def alert_tp(self, *a):
        self.calls.append(("tp",) + a)

    def alert_sl(self, *a):
        self.calls.append(("sl",) + a)

    def alert_counter(self, *a):
        self.calls.append(("counter",) + a)


def _sig():
    return Signal("005930", "long", 70200, 69200, 6,
                  {"L1": True}, atr=500, rvol=1.8, prev_close=69000)


def _trader(client=None, notifier=None, cfg=None):
    return tr.AutoTrader(cfg or _cfg(), client or MockClient(), notifier or MockNotifier())


# ── 진입 ────────────────────────────────────────────────
def test_execute_signal_places_buy_and_tps():
    c = MockClient(cash=10_000_000)
    n = MockNotifier()
    ei = _trader(c, n).execute_signal(_sig(), "삼성전자")
    assert ei is not None
    assert ei.entry_qty == 100
    assert ei.holding_qty == 100
    assert c.buys == [("005930", 100, None)]            # 시장가 매수
    assert len(c.sells) == 3                            # TP1/2/3 지정가
    assert c.sells[0] == ("005930", 33, 71200)
    assert ei.order_ids["entry"] == "BUY1"
    assert ("entry",) == n.calls[0][:1]


def test_execute_signal_none_when_cash_too_small():
    ei = _trader(MockClient(cash=10_000)).execute_signal(_sig(), "삼성전자")
    assert ei is None


def test_execute_signal_none_when_buy_fails():
    c = MockClient()
    c.place_buy_order = lambda *a, **k: None
    assert _trader(c).execute_signal(_sig(), "삼성전자") is None


# ── TP 체결 감지 + 추적 SL ──────────────────────────────
def _entry_info():
    return tr.EntryInfo(symbol="005930", name="삼성전자", side="long",
                        entry_price=70200, entry_qty=100, holding_qty=100,
                        sl_price=69200, tp1_price=71000, tp2_price=71700,
                        tp3_price=72700, qty1=33, qty2=33, qty3=34, risk_r=1000,
                        order_ids={"entry": "B", "tp1": "S1", "tp2": "S2", "tp3": "S3"})


def test_on_fill_tp1_updates_sl_breakeven():
    c = MockClient()
    c.set_position_qty(67)            # 33주 체결 (TP1)
    n = MockNotifier()
    ei = _trader(c, n).on_fill_check(_entry_info())
    assert ei.tp_count == 1
    assert ei.holding_qty == 67
    assert ei.sl_price == 70700       # entry + 0.5R
    assert any(call[0] == "tp" for call in n.calls)


def test_on_fill_tp2_updates_sl_profit():
    c = MockClient()
    c.set_position_qty(34)            # 66주 체결 (TP1+TP2)
    ei = _trader(c).on_fill_check(_entry_info())
    assert ei.tp_count == 2
    assert ei.sl_price == 71700       # entry + 1.5R


def test_on_fill_no_change():
    c = MockClient()
    c.set_position_qty(100)
    ei = _trader(c).on_fill_check(_entry_info())
    assert ei.tp_count == 0
    assert ei.sl_price == 69200


def test_on_fill_full_close_returns_none():
    c = MockClient()
    c.set_position_qty(0)
    assert _trader(c).on_fill_check(_entry_info()) is None


# ── SL / 청산 ───────────────────────────────────────────
def test_monitor_sl_hit_closes():
    c = MockClient()
    c.set_position_qty(100)
    n = MockNotifier()
    t = _trader(c, n)
    result = t.monitor(_entry_info(), current_price=69100)   # SL 69200 하회
    assert result is None
    assert any(s[2] is None for s in c.sells)               # 시장가 청산 매도
    assert any(call[0] == "sl" for call in n.calls)


def test_monitor_counter_closes():
    c = MockClient()
    c.set_position_qty(100)
    n = MockNotifier()
    t = _trader(c, n)
    result = t.monitor(_entry_info(), current_price=70500, counter=True,
                       counter_reason="CHoCH 약세 전환")
    assert result is None
    assert any(call[0] == "counter" for call in n.calls)
    assert len(c.cancels) >= 1                              # 잔여 TP 취소


def test_monitor_holds_when_safe():
    c = MockClient()
    c.set_position_qty(100)
    t = _trader(c)
    result = t.monitor(_entry_info(), current_price=70500)
    assert result is not None
    assert result.holding_qty == 100


def test_close_all_cancels_and_sells():
    c = MockClient()
    n = MockNotifier()
    t = _trader(c, n)
    t.close_all(_entry_info(), "SL", price=69100)
    assert len(c.cancels) == 3                              # tp1/2/3 취소
    assert c.sells[-1][1] == 100                            # 보유 전량 매도
    assert c.sells[-1][2] is None                           # 시장가
