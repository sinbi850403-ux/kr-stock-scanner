"""indicators.py 테스트 — EMA/ATR/RSI/MACD/OBV/VWAP/RVOL."""
import math
import pandas as pd
import pytest
from tests.conftest import make_df
import indicators as ind


# ── EMA ─────────────────────────────────────────────────
def test_ema_constant():
    s = pd.Series([100.0] * 30)
    out = ind.ema(s, 10)
    assert out.iloc[-1] == pytest.approx(100.0)
    assert len(out) == 30


def test_ema_known_value():
    # adjust=False EMA, span=3 → k=0.5
    s = pd.Series([1.0, 2.0, 3.0])
    out = ind.ema(s, 3)
    # e0=1, e1=1.5, e2=2.25
    assert out.iloc[0] == pytest.approx(1.0)
    assert out.iloc[1] == pytest.approx(1.5)
    assert out.iloc[2] == pytest.approx(2.25)


def test_ema_uptrend_above_price_lag():
    s = pd.Series([float(i) for i in range(1, 51)])
    out = ind.ema(s, 10)
    # 상승 추세에서 EMA는 현재가보다 낮음(후행)
    assert out.iloc[-1] < s.iloc[-1]


# ── ATR ─────────────────────────────────────────────────
def test_atr_constant_range():
    # H-L 고정 10, 갭 없음 → ATR = 10
    df = make_df([100.0] * 30,
                 highs=[105.0] * 30, lows=[95.0] * 30, opens=[100.0] * 30)
    out = ind.atr(df["high"], df["low"], df["close"], 14)
    assert out.iloc[-1] == pytest.approx(10.0, abs=0.5)


def test_atr_length_preserved():
    df = make_df([100 + i for i in range(30)])
    out = ind.atr(df["high"], df["low"], df["close"], 14)
    assert len(out) == 30


def test_atr_positive():
    df = make_df([100 + (i % 5) for i in range(40)])
    out = ind.atr(df["high"], df["low"], df["close"], 14)
    assert (out.dropna() >= 0).all()


# ── RSI ─────────────────────────────────────────────────
def test_rsi_all_up_high():
    s = pd.Series([float(i) for i in range(1, 60)])
    out = ind.rsi(s, 14)
    assert out.iloc[-1] > 90  # 지속 상승 → RSI 매우 높음


def test_rsi_all_down_low():
    s = pd.Series([float(i) for i in range(60, 1, -1)])
    out = ind.rsi(s, 14)
    assert out.iloc[-1] < 10  # 지속 하락 → RSI 매우 낮음


def test_rsi_range_bounded():
    s = pd.Series([100 + 10 * math.sin(i / 3) for i in range(80)])
    out = ind.rsi(s, 14).dropna()
    assert (out >= 0).all() and (out <= 100).all()


# ── MACD ────────────────────────────────────────────────
def test_macd_constant_zero():
    s = pd.Series([100.0] * 60)
    m, sig, hist = ind.macd(s, 12, 26, 9)
    assert m.iloc[-1] == pytest.approx(0.0, abs=1e-9)
    assert sig.iloc[-1] == pytest.approx(0.0, abs=1e-9)
    assert hist.iloc[-1] == pytest.approx(0.0, abs=1e-9)


def test_macd_uptrend_positive():
    s = pd.Series([float(i) for i in range(1, 80)])
    m, sig, hist = ind.macd(s, 12, 26, 9)
    assert m.iloc[-1] > 0  # 상승 추세 → MACD > 0


def test_macd_returns_three():
    s = pd.Series([float(i) for i in range(1, 80)])
    res = ind.macd(s, 12, 26, 9)
    assert len(res) == 3


# ── OBV ─────────────────────────────────────────────────
def test_obv_rising_on_up_closes():
    close = pd.Series([10, 11, 12, 13])
    vol = pd.Series([100, 100, 100, 100])
    out = ind.obv(close, vol)
    # 첫봉 0, 이후 매봉 +100 → [0,100,200,300]
    assert list(out) == [0, 100, 200, 300]


def test_obv_falling_on_down_closes():
    close = pd.Series([13, 12, 11, 10])
    vol = pd.Series([100, 100, 100, 100])
    out = ind.obv(close, vol)
    assert list(out) == [0, -100, -200, -300]


def test_obv_flat_on_equal_closes():
    close = pd.Series([10, 10, 10])
    vol = pd.Series([100, 100, 100])
    out = ind.obv(close, vol)
    assert list(out) == [0, 0, 0]


# ── 롤링 VWAP ───────────────────────────────────────────
def test_vwap_rolling_equal_weight():
    # 가격·거래량 동일 → VWAP = 가격
    df = make_df([100.0] * 30, highs=[100.0] * 30, lows=[100.0] * 30, opens=[100.0] * 30,
                 vols=[500.0] * 30)
    out = ind.vwap_rolling(df["high"], df["low"], df["close"], df["volume"], 20)
    assert out.iloc[-1] == pytest.approx(100.0)


def test_vwap_rolling_weighted():
    # 마지막 봉 거래량 폭증 → VWAP가 마지막 hlc3 쪽으로 당겨짐
    closes = [100.0] * 19 + [110.0]
    df = make_df(closes, highs=closes, lows=closes, opens=closes,
                 vols=[100.0] * 19 + [10000.0])
    out = ind.vwap_rolling(df["high"], df["low"], df["close"], df["volume"], 20)
    assert 108.0 < out.iloc[-1] < 110.0


# ── RVOL ────────────────────────────────────────────────
def test_rvol_surge():
    vols = [100.0] * 19 + [300.0]
    df = make_df([100.0] * 20, vols=vols)
    out = ind.rvol(df["volume"], 20)
    assert out.iloc[-1] == pytest.approx(300.0 / 105.0, rel=0.05)  # 평균 약간 상승


def test_rvol_normal_near_one():
    df = make_df([100.0] * 40, vols=[100.0] * 40)
    out = ind.rvol(df["volume"], 20)
    assert out.iloc[-1] == pytest.approx(1.0)


def test_rvol_zero_avg_safe():
    # 거래량 0 → 0으로 나누기 방지 (NaN/inf 아님)
    df = make_df([100.0] * 25, vols=[0.0] * 25)
    out = ind.rvol(df["volume"], 20)
    assert not math.isinf(out.iloc[-1])
