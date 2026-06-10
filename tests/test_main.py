"""main.py 테스트 — 상태머신·재시작복구·킬스위치·상태영속화 (mock)."""
import json
from datetime import datetime
import pytz
import pytest
from config import Config
import main as m
from strategy import Signal
from trader import EntryInfo

KST = pytz.timezone("Asia/Seoul")


def _dt(h, mi, d=9):
    return KST.localize(datetime(2026, 6, d, h, mi))


def _cfg(**kw):
    base = dict(app_key="k", app_secret="s", cano="12345678",
                telegram_token="t", telegram_chat="c")
    base.update(kw)
    return Config(**base)


class MockClient:
    def __init__(self, cash=10_000_000, holdings=None, price=70200, prev=69000):
        self.cash = cash
        self.holdings = holdings or []
        self._price = price
        self._prev = prev

    def get_balance(self):
        return self.cash, self.holdings

    def get_position(self, symbol):
        for h in self.holdings:
            if h["symbol"] == symbol:
                return h
        return None

    def get_current_price(self, symbol):
        return {"price": self._price, "prev_close": self._prev,
                "upper": self._prev * 1.3, "lower": self._prev * 0.7, "change_pct": 0}

    def get_candles(self, code, period="D", count=100):
        import pandas as pd
        return pd.DataFrame()


class MockScanner:
    def __init__(self, signals=None):
        self.signals = signals or []
        self.scanned = 0

    def scan(self, skip_symbols=None, now=None):
        self.scanned += 1
        return self.signals


class MockTrader:
    def __init__(self):
        self.executed = []
        self.monitored = []
        self.next_ei = None
        self.monitor_returns = "same"   # "same" | None

    def execute_signal(self, sig, name, now=None):
        self.executed.append(sig.symbol)
        return self.next_ei

    def monitor(self, ei, price, counter=False, counter_reason=""):
        self.monitored.append((ei.symbol, price, counter))
        return None if (self.monitor_returns is None or counter) else ei


class MockNotifier:
    def __init__(self):
        self.calls = []

    def alert_scan_signal(self, *a, **k):
        self.calls.append("scan")

    def alert_entry(self, *a, **k):
        self.calls.append("entry")

    def alert_error(self, *a, **k):
        self.calls.append("error")


def _bot(tmp_path, client=None, scanner=None, trader=None, notifier=None, cfg=None):
    return m.TradingBot(
        cfg or _cfg(),
        client or MockClient(),
        scanner or MockScanner(),
        trader or MockTrader(),
        notifier or MockNotifier(),
        state_path=str(tmp_path / "state.json"))


def _entry_info(symbol="000100"):
    return EntryInfo(symbol=symbol, name="에이", side="long", entry_price=70200,
                     entry_qty=100, holding_qty=100, sl_price=69200,
                     tp1_price=71000, tp2_price=71700, tp3_price=72700,
                     qty1=33, qty2=33, qty3=34, risk_r=1000,
                     order_ids={"entry": "B"})


def _signal(symbol="000100", score=6):
    return Signal(symbol, "long", 70200, 69200, score, {"L1": True}, 500, 1.8, 69000)


# ── 상태 영속화 ─────────────────────────────────────────
def test_save_load_roundtrip(tmp_path):
    bot = _bot(tmp_path)
    bot.entry_info = _entry_info()
    bot.pending_signals = [("에이", _signal())]
    bot.daily_trade_count = 2
    bot.save_state()

    bot2 = _bot(tmp_path)
    bot2.load_state()
    assert bot2.entry_info.symbol == "000100"
    assert bot2.entry_info.holding_qty == 100
    assert bot2.daily_trade_count == 2
    assert bot2.pending_signals[0][1].symbol == "000100"


# ── 일일 리셋 ───────────────────────────────────────────
def test_daily_reset(tmp_path):
    bot = _bot(tmp_path)
    bot.daily_trade_count = 3
    bot.kill_switch = True
    bot._daily_reset_if_needed(_dt(9, 0))
    assert bot.daily_trade_count == 0
    assert bot.kill_switch is False
    assert bot.start_balance == 10_000_000


# ── 재시작 복구 ─────────────────────────────────────────
def test_recovery_clears_when_kis_empty(tmp_path):
    client = MockClient(holdings=[])
    bot = _bot(tmp_path, client=client)
    bot.entry_info = _entry_info()
    bot.save_state()
    bot.startup_recovery()
    assert bot.entry_info is None        # KIS에 포지션 없음 → 청산 처리


def test_recovery_syncs_qty(tmp_path):
    client = MockClient(holdings=[{"symbol": "000100", "qty": 67, "avg": 70200, "price": 70500}])
    bot = _bot(tmp_path, client=client)
    bot.entry_info = _entry_info()
    bot.save_state()
    bot.startup_recovery()
    assert bot.entry_info.holding_qty == 67   # 실제 잔고로 동기화


def test_recovery_no_telegram_for_manual_position(tmp_path, caplog):
    """수동 매수 포지션은 텔레그램 에러 없이 로그 경고만 남긴다."""
    import logging
    client = MockClient(holdings=[{"symbol": "999990", "qty": 5, "avg": 100, "price": 100}])
    n = MockNotifier()
    bot = _bot(tmp_path, client=client, notifier=n)
    with caplog.at_level(logging.WARNING):
        bot.startup_recovery()
    assert "error" not in n.calls         # 텔레그램 에러 알림 없어야 함
    assert "999990" in caplog.text        # 로그엔 종목코드 남아야 함


# ── run_cycle 분기 ──────────────────────────────────────
def test_run_cycle_manages_position(tmp_path):
    client = MockClient(holdings=[{"symbol": "000100", "qty": 100, "avg": 70200, "price": 70500}],
                        price=70500)
    t = MockTrader()
    bot = _bot(tmp_path, client=client, trader=t)
    bot.entry_info = _entry_info()
    bot._check_counter = lambda sym, now: (False, "")
    bot.run_cycle(now=_dt(10, 30))
    assert len(t.monitored) == 1          # 포지션 감시 호출


def test_run_cycle_enters_from_pending(tmp_path):
    t = MockTrader()
    t.next_ei = _entry_info()
    bot = _bot(tmp_path, trader=t)
    bot.start_balance = 10_000_000
    bot.last_reset_date = "20260609"      # 리셋 스킵
    bot.pending_signals = [("에이", _signal())]
    bot.run_cycle(now=_dt(9, 20))         # 진입 윈도우
    assert t.executed == ["000100"]
    assert bot.entry_info is not None
    assert bot.daily_trade_count == 1


def test_run_cycle_postclose_scans(tmp_path):
    sc = MockScanner(signals=[("에이", _signal())])
    n = MockNotifier()
    bot = _bot(tmp_path, scanner=sc, notifier=n)
    bot.last_reset_date = "20260609"
    bot.run_cycle(now=_dt(15, 45))        # 마감 후
    assert sc.scanned == 1
    assert bot.pending_signals             # 익일 진입 후보 저장
    assert "scan" in n.calls


def test_run_cycle_skips_on_kill_switch(tmp_path):
    sc = MockScanner(signals=[("에이", _signal())])
    bot = _bot(tmp_path, scanner=sc)
    bot.last_reset_date = "20260609"
    bot.kill_switch = True
    bot.run_cycle(now=_dt(15, 45))
    assert sc.scanned == 0                 # 아무것도 안 함


def test_run_cycle_weekend_idle(tmp_path):
    sc = MockScanner(signals=[("에이", _signal())])
    bot = _bot(tmp_path, scanner=sc)
    bot.last_reset_date = "20260613"
    bot.run_cycle(now=_dt(15, 45, d=13))   # 토요일
    assert sc.scanned == 0


# ── 킬스위치 ────────────────────────────────────────────
def test_kill_switch_on_big_loss(tmp_path):
    n = MockNotifier()
    bot = _bot(tmp_path, notifier=n, cfg=_cfg(daily_max_loss_pct=0.03))
    bot.start_balance = 10_000_000        # 한도 300,000원
    bot._on_close(pnl=-400_000, now=_dt(11, 0))
    assert bot.kill_switch is True
    assert "error" in n.calls


def test_no_kill_switch_small_loss(tmp_path):
    bot = _bot(tmp_path, cfg=_cfg(daily_max_loss_pct=0.03))
    bot.start_balance = 10_000_000
    bot._on_close(pnl=-100_000, now=_dt(11, 0))
    assert bot.kill_switch is False


def test_run_cycle_intraday_alert_only(tmp_path):
    sc = MockScanner(signals=[("에이", _signal())])
    n = MockNotifier()
    bot = _bot(tmp_path, scanner=sc, notifier=n)
    bot.last_reset_date = "20260609"
    bot.run_cycle(now=_dt(10, 30))      # 장중 → 잠정 알림만
    assert sc.scanned == 1
    assert "scan" in n.calls
    assert bot.entry_info is None        # 주문 없음
    assert bot.pending_signals == []     # 장중은 pending 저장 안 함


def test_manage_position_closes_and_counts(tmp_path):
    client = MockClient(
        holdings=[{"symbol": "000100", "qty": 100, "avg": 70200, "price": 69000}],
        price=69000)
    t = MockTrader()
    t.monitor_returns = None             # 청산됨
    bot = _bot(tmp_path, client=client, trader=t, cfg=_cfg(daily_max_loss_pct=0.03))
    bot.entry_info = _entry_info()
    bot.start_balance = 10_000_000
    bot.last_reset_date = "20260609"
    bot._check_counter = lambda sym, now: (False, "")
    bot.run_cycle(now=_dt(10, 30))
    assert bot.entry_info is None
    assert bot.daily_trade_count == 1
