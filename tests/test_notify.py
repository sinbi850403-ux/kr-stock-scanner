"""notify.py 테스트 — 텔레그램 포맷·발송 (mock)."""
import pytest
from config import Config
import notify
from risk import TradeParams
from strategy import Signal


def _cfg():
    return Config(telegram_token="T", telegram_chat="C")


class FakeSession:
    def __init__(self, fail=False):
        self.fail = fail
        self.sent = []

    def post(self, url, json=None, timeout=None):
        if self.fail:
            raise notify.requests.RequestException("net")
        self.sent.append((url, json))

        class R:
            status_code = 200
        return R()


# ── send ────────────────────────────────────────────────
def test_send_success():
    s = FakeSession()
    n = notify.Notifier(_cfg(), session=s)
    assert n.send("hello") is True
    url, payload = s.sent[0]
    assert "botT" in url
    assert payload["chat_id"] == "C"
    assert payload["text"] == "hello"
    assert payload["parse_mode"] == "HTML"


def test_send_failure_returns_false():
    n = notify.Notifier(_cfg(), session=FakeSession(fail=True))
    assert n.send("x") is False


# ── 포맷 ────────────────────────────────────────────────
def _signal():
    return Signal(symbol="005930", direction="long", entry_price=70200,
                  sl_price=69200, score=5,
                  layers={"L1": True, "L2": True, "L3": True, "L4": True,
                          "L5": True, "L6": False},
                  atr=500, rvol=1.8, prev_close=69000)


def test_fmt_scan_signal_has_fields():
    txt = notify.fmt_scan_signal(_signal(), "삼성전자", provisional=True)
    assert "삼성전자" in txt
    assert "005930" in txt
    assert "5/6" in txt
    assert "70,200" in txt
    assert "잠정" in txt


def test_fmt_scan_signal_confirmed():
    txt = notify.fmt_scan_signal(_signal(), "삼성전자", provisional=False)
    assert "확정" in txt


def test_fmt_entry_has_tp_sl():
    tp = TradeParams(entry_price=70200, sl_price=69200, tp1_price=71000,
                     tp2_price=71700, tp3_price=72700, total_qty=100,
                     qty1=33, qty2=33, qty3=34, risk_krw=100000, risk_r=1000)
    txt = notify.fmt_entry("005930", "삼성전자", tp)
    assert "71,000" in txt and "71,700" in txt and "72,700" in txt
    assert "69,200" in txt
    assert "100" in txt


def test_fmt_tp():
    txt = notify.fmt_tp(1, "005930", 33, 71000, 26400)
    assert "TP1" in txt and "33" in txt


def test_fmt_sl():
    txt = notify.fmt_sl("005930", 70200, 69200, -100000)
    assert "손절" in txt or "SL" in txt


def test_fmt_counter():
    txt = notify.fmt_counter("005930", 70200, 70100, "CHoCH 약세 전환")
    assert "역신호" in txt or "청산" in txt
    assert "CHoCH" in txt


# ── alert_* 발송 연동 ───────────────────────────────────
def test_alert_entry_sends():
    s = FakeSession()
    n = notify.Notifier(_cfg(), session=s)
    tp = TradeParams(70200, 69200, 71000, 71700, 72700, 100, 33, 33, 34, 100000, 1000)
    n.alert_entry("005930", "삼성전자", tp)
    assert len(s.sent) == 1
