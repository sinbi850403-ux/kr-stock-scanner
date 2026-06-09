"""strategy.py 테스트 — 신호 생성 / 역신호 청산 / 일봉 방향."""
import pytest
from tests.conftest import make_df
from config import Config
import strategy


def _rising(n, start=100.0, step=1.0):
    return make_df([start + i * step for i in range(n)])


def _falling(n, start=250.0, step=1.5):
    return make_df([start - i * step for i in range(n)])


# ── generate_signal ─────────────────────────────────────
def test_signal_none_below_threshold():
    cfg = Config(threshold=5)   # 단조상승은 3점 → None
    assert strategy.generate_signal("005930", _rising(100), _rising(60),
                                    _rising(30), cfg, prev_close=197.0) is None


def test_signal_none_insufficient_data():
    cfg = Config(threshold=3)
    assert strategy.generate_signal("005930", _rising(30), _rising(60),
                                    _rising(30), cfg) is None


def test_signal_generated_when_score_met():
    cfg = Config(threshold=3)   # 단조상승 3점 → 신호
    sig = strategy.generate_signal("005930", _rising(100), _rising(60),
                                   _rising(30), cfg, prev_close=197.0)
    assert sig is not None
    assert sig.direction == "long"
    assert sig.symbol == "005930"
    assert sig.entry_price == pytest.approx(199.0)
    assert sig.sl_price < sig.entry_price
    assert sig.score == 3


# ── 일봉 방향 ───────────────────────────────────────────
def test_daily_direction_up():
    assert strategy.daily_direction(_rising(100), Config()) == 1


def test_daily_direction_down():
    assert strategy.daily_direction(_falling(100), Config()) == -1


def test_daily_direction_flat():
    assert strategy.daily_direction(make_df([100.0] * 100), Config()) == 0


# ── 역신호 청산 ─────────────────────────────────────────
def test_counter_exit_on_bear_stack():
    cfg = Config()
    should, reason = strategy.counter_exit(_falling(100), _rising(60), _rising(30), cfg)
    assert should is True
    assert "역배열" in reason


def test_counter_exit_false_in_uptrend():
    cfg = Config()
    should, _ = strategy.counter_exit(_rising(100), _rising(60), _rising(30), cfg)
    assert should is False


def test_counter_exit_on_weekly_trend_loss():
    cfg = Config()
    # 일봉은 상승이나 주봉이 하락 → 상위추세 이탈로 청산
    should, reason = strategy.counter_exit(_rising(100), _falling(60), _rising(30), cfg)
    assert should is True
    assert "상위" in reason


def test_counter_exit_insufficient_data():
    cfg = Config()
    should, _ = strategy.counter_exit(_rising(30), _rising(60), _rising(30), cfg)
    assert should is False
