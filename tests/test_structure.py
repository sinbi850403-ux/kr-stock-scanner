"""structure.py 테스트 — 피봇/피보황금구간/오더블록/BOS·CHoCH."""
import pytest
from tests.conftest import make_df
from config import Config
import structure as st


# ── 피봇 ────────────────────────────────────────────────
def test_find_pivot_high():
    highs = [1, 2, 5, 2, 1]
    lows = [0, 0, 0, 0, 0]
    ph, pl = st.find_pivots(highs, lows, 2)
    assert ph == [(2, 5)]


def test_find_pivot_low():
    highs = [9, 9, 9, 9, 9]
    lows = [5, 4, 1, 4, 5]
    ph, pl = st.find_pivots(highs, lows, 2)
    assert pl == [(2, 1)]


def test_find_pivots_none_in_monotonic():
    highs = [float(i) for i in range(20)]
    lows = [float(i) - 1 for i in range(20)]
    ph, pl = st.find_pivots(highs, lows, 2)
    assert ph == [] and pl == []


# ── 황금구간 ────────────────────────────────────────────
def test_golden_zone_calc():
    gz_top, gz_bot = st.golden_zone(200.0, 100.0)
    assert gz_top == pytest.approx(150.0)       # 0.5 retr
    assert gz_bot == pytest.approx(138.2)       # 0.618 retr


def test_golden_zone_zero_range():
    assert st.golden_zone(100.0, 100.0) == (None, None)


def test_in_zone_inside():
    assert st.in_zone(145.0, 140.0, 150.0, 138.2) is True


def test_in_zone_outside():
    assert st.in_zone(160.0, 152.0, 150.0, 138.2) is False


def test_in_zone_none():
    assert st.in_zone(100.0, 99.0, None, None) is False


# ── 오더블록 ────────────────────────────────────────────
def _ob_df(last_close):
    opens = [100.0] * 18 + [100.0, 98.0, 105.0]
    highs = [101.0] * 18 + [100.0, 105.0, 107.0]
    lows = [99.0] * 18 + [97.0, 98.0, 105.0]
    closes = [100.0] * 18 + [98.0, 105.0, last_close]
    vols = [1000.0] * 19 + [3000.0, 1000.0]
    return make_df(closes, opens=opens, highs=highs, lows=lows, vols=vols)


def test_active_bull_ob_detected():
    cfg = Config()
    ob = st.active_bull_ob(_ob_df(106.0), cfg)
    assert ob == (100.0, 97.0)   # 직전봉(idx18) high/low


def test_active_bull_ob_mitigated():
    cfg = Config()
    # 마지막 봉이 OB 하단(97) 아래로 마감 → 미티게이션 → None
    opens = [100.0] * 18 + [100.0, 98.0, 105.0]
    highs = [101.0] * 18 + [100.0, 105.0, 105.0]
    lows = [99.0] * 18 + [97.0, 98.0, 95.0]
    closes = [100.0] * 18 + [98.0, 105.0, 96.0]
    vols = [1000.0] * 19 + [3000.0, 1000.0]
    df = make_df(closes, opens=opens, highs=highs, lows=lows, vols=vols)
    assert st.active_bull_ob(df, cfg) is None


def test_active_bull_ob_none_when_flat():
    cfg = Config()
    df = make_df([100.0] * 30, highs=[101.0] * 30, lows=[99.0] * 30,
                 opens=[100.0] * 30, vols=[1000.0] * 30)
    assert st.active_bull_ob(df, cfg) is None


# ── BOS / CHoCH 상태머신 ────────────────────────────────
def _hlc(closes):
    highs = [c + 1 for c in closes]
    lows = [c - 1 for c in closes]
    return highs, lows, [float(c) for c in closes]


def test_structure_os_bullish():
    closes = [10, 12, 14, 12, 11, 13, 16, 16, 16]
    highs, lows, cl = _hlc(closes)
    os, choch = st.structure_os(highs, lows, cl, 2)
    assert os == 1
    assert choch is False


def test_structure_os_bear_choch():
    closes = [10, 12, 14, 12, 11, 13, 16, 15, 12, 14, 15, 13, 10]
    highs, lows, cl = _hlc(closes)
    os, choch = st.structure_os(highs, lows, cl, 2)
    assert os == -1
    assert choch is True


def test_structure_os_neutral_monotonic():
    closes = [float(i) for i in range(20)]
    highs, lows, cl = _hlc(closes)
    os, choch = st.structure_os(highs, lows, cl, 2)
    assert os == 0


# ── analyze 통합 ────────────────────────────────────────
def test_analyze_returns_state():
    cfg = Config(piv_len=2)
    closes = [10, 12, 14, 12, 11, 13, 16, 15, 12, 14, 15, 13, 10]
    df = make_df(closes, highs=[c + 1 for c in closes],
                 lows=[c - 1 for c in closes], opens=closes)
    state = st.analyze(df, cfg)
    assert state.os == -1
    assert state.bear_choch is True
    assert state.swing_high is not None
    assert state.swing_low is not None
