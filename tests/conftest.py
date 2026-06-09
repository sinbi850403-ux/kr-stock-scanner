"""공용 테스트 픽스처 — 합성 캔들 데이터."""
import pandas as pd
import pytest


def make_df(closes, opens=None, highs=None, lows=None, vols=None):
    """종가 리스트로 OHLCV DataFrame 생성. 미지정 항목은 종가 기반 합성."""
    n = len(closes)
    closes = [float(x) for x in closes]
    opens = [float(x) for x in opens] if opens is not None else [closes[max(i - 1, 0)] for i in range(n)]
    highs = [float(x) for x in highs] if highs is not None else [max(o, c) * 1.005 for o, c in zip(opens, closes)]
    lows = [float(x) for x in lows] if lows is not None else [min(o, c) * 0.995 for o, c in zip(opens, closes)]
    vols = [float(x) for x in vols] if vols is not None else [1000.0] * n
    return pd.DataFrame({
        "open": opens, "high": highs, "low": lows, "close": closes, "volume": vols,
    })


@pytest.fixture
def make_df_factory():
    return make_df


@pytest.fixture
def uptrend_df():
    """깨끗한 우상승 — EMA 정배열 보장 (100봉)."""
    closes = [100 + i * 1.5 for i in range(100)]
    return make_df(closes)


@pytest.fixture
def downtrend_df():
    """깨끗한 우하락 (100봉)."""
    closes = [250 - i * 1.5 for i in range(100)]
    return make_df(closes)


@pytest.fixture
def flat_df():
    """완전 횡보 — 상수 가격 (100봉)."""
    return make_df([100.0] * 100)
