"""
기술 지표 — pandas Series 기반 순수 함수.

Pine 정합성:
  - EMA = ewm(span=N, adjust=False)
  - ATR/RSI = Wilder's RMA = ewm(alpha=1/N, adjust=False)
  - MACD = ema(fast)-ema(slow), signal=ema(macd,sig), hist=macd-signal
  - VWAP(일봉 대체) = N일 롤링 Σ(hlc3·vol)/Σ(vol)
"""
import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _rma(series: pd.Series, period: int) -> pd.Series:
    """Wilder's smoothing."""
    return series.ewm(alpha=1.0 / period, adjust=False).mean()


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    pc = close.shift()
    tr = pd.concat([high - low, (high - pc).abs(), (low - pc).abs()], axis=1).max(axis=1)
    return _rma(tr, period)


def rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff().fillna(0.0)
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = _rma(gain, period)
    avg_loss = _rma(loss, period)
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    # 손실 0 & 이익>0 → 100 ; 무변동 → 50
    out = out.mask((avg_loss == 0) & (avg_gain > 0), 100.0)
    out = out.mask((avg_loss == 0) & (avg_gain == 0), 50.0)
    return out


def macd(close: pd.Series, fast: int, slow: int, signal: int):
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.diff().fillna(0.0))
    return (direction * volume).cumsum().astype(float)


def vwap_rolling(high: pd.Series, low: pd.Series, close: pd.Series,
                 volume: pd.Series, window: int) -> pd.Series:
    """일봉 세션 VWAP 대체 — N일 롤링 거래량가중평균."""
    hlc3 = (high + low + close) / 3.0
    pv = (hlc3 * volume).rolling(window, min_periods=1).sum()
    vv = volume.rolling(window, min_periods=1).sum()
    return pv / vv.replace(0, np.nan)


def rvol(volume: pd.Series, window: int) -> pd.Series:
    """상대거래량 = 현재거래량 / N일 평균거래량."""
    avg = volume.rolling(window, min_periods=1).mean()
    return volume / avg.replace(0, np.nan)
