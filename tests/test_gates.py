"""gates.py 테스트 — 갭/VI/상하한/시간/한도/중복."""
from datetime import datetime
import pytz
import pytest
from config import Config
import gates

KST = pytz.timezone("Asia/Seoul")


def _dt(y, mo, d, h, mi):
    return KST.localize(datetime(y, mo, d, h, mi))


# ── 갭 ──────────────────────────────────────────────────
def test_gap_pass():
    ok, _ = gates.check_gap_filter(102.0, 100.0, Config())
    assert ok is True


def test_gap_fail():
    ok, reason = gates.check_gap_filter(104.0, 100.0, Config())  # +4% > 3%
    assert ok is False and "갭" in reason


def test_gap_exactly_threshold_pass():
    ok, _ = gates.check_gap_filter(103.0, 100.0, Config())  # 정확히 3%
    assert ok is True


def test_gap_zero_prev_safe():
    ok, _ = gates.check_gap_filter(100.0, 0.0, Config())
    assert ok is True  # 0 division 방지, 통과


# ── 상/하한가 ───────────────────────────────────────────
def test_limit_up_veto():
    ok, reason = gates.check_limit_up_down(128.0, 100.0, Config())  # +28%
    assert ok is False and "상한" in reason


def test_limit_down_veto():
    ok, reason = gates.check_limit_up_down(71.0, 100.0, Config())  # -29%
    assert ok is False and "하한" in reason


def test_limit_normal_pass():
    ok, _ = gates.check_limit_up_down(105.0, 100.0, Config())
    assert ok is True


# ── VI (경고만) ─────────────────────────────────────────
def test_vi_warns_but_passes():
    ok, reason = gates.check_vi_limit(110.0, 100.0, Config())  # +10% ≥ 9%
    assert ok is True
    assert "VI" in reason


def test_vi_normal_no_warn():
    ok, reason = gates.check_vi_limit(103.0, 100.0, Config())
    assert ok is True


# ── 거래시간 ────────────────────────────────────────────
def test_hours_ok():
    ok, _ = gates.check_trading_hours(_dt(2026, 6, 9, 10, 30), Config())  # 화 10:30
    assert ok is True


def test_hours_before_entry_start():
    ok, _ = gates.check_trading_hours(_dt(2026, 6, 9, 9, 5), Config())   # 09:05
    assert ok is False


def test_hours_after_entry_end():
    ok, _ = gates.check_trading_hours(_dt(2026, 6, 9, 15, 25), Config())  # 15:25
    assert ok is False


def test_hours_weekend():
    ok, reason = gates.check_trading_hours(_dt(2026, 6, 13, 10, 30), Config())  # 토
    assert ok is False


def test_hours_holiday():
    cfg = Config()
    ok, _ = gates.check_trading_hours(_dt(2026, 6, 9, 10, 30), cfg, holidays={"20260609"})
    assert ok is False


# ── 거래/포지션 한도 ────────────────────────────────────
def test_daily_trade_limit_ok():
    ok, _ = gates.check_daily_trade_limit(2, Config())
    assert ok is True


def test_daily_trade_limit_exceed():
    ok, _ = gates.check_daily_trade_limit(3, Config())
    assert ok is False


def test_position_limit_ok():
    ok, _ = gates.check_position_limit(0, Config())
    assert ok is True


def test_position_limit_exceed():
    ok, _ = gates.check_position_limit(1, Config())
    assert ok is False


# ── 중복진입 ────────────────────────────────────────────
def test_duplicate_within_1h():
    now = _dt(2026, 6, 9, 11, 0)
    log = [{"symbol": "005930", "time": _dt(2026, 6, 9, 10, 30)}]
    ok, _ = gates.check_duplicate_entry("005930", log, now, Config())
    assert ok is False


def test_duplicate_after_1h():
    now = _dt(2026, 6, 9, 12, 0)
    log = [{"symbol": "005930", "time": _dt(2026, 6, 9, 10, 30)}]
    ok, _ = gates.check_duplicate_entry("005930", log, now, Config())
    assert ok is True


def test_duplicate_other_symbol():
    now = _dt(2026, 6, 9, 10, 40)
    log = [{"symbol": "000660", "time": _dt(2026, 6, 9, 10, 30)}]
    ok, _ = gates.check_duplicate_entry("005930", log, now, Config())
    assert ok is True


# ── validate_signal 통합 ────────────────────────────────
def test_validate_all_pass():
    ok, reason = gates.validate_signal(
        open_price=101.0, prev_close=100.0, current_price=102.0,
        now=_dt(2026, 6, 9, 10, 30), daily_trade_count=0, current_positions=0,
        symbol="005930", daily_log=[], cfg=Config())
    assert ok is True


def test_validate_fail_on_gap():
    ok, reason = gates.validate_signal(
        open_price=106.0, prev_close=100.0, current_price=106.0,
        now=_dt(2026, 6, 9, 10, 30), daily_trade_count=0, current_positions=0,
        symbol="005930", daily_log=[], cfg=Config())
    assert ok is False and "갭" in reason


def test_validate_fail_on_position():
    ok, reason = gates.validate_signal(
        open_price=101.0, prev_close=100.0, current_price=102.0,
        now=_dt(2026, 6, 9, 10, 30), daily_trade_count=0, current_positions=1,
        symbol="005930", daily_log=[], cfg=Config())
    assert ok is False
