"""risk.py 테스트 — 호가단위/사이징/3분할/추적SL/손절계산."""
import pytest
from config import Config
import risk


# ── 호가단위 반올림 (KRX 2023 개편) ─────────────────────
def test_tick_under_2000():
    assert risk.round_price_to_tick(1500) == 1500


def test_tick_under_5000():
    assert risk.round_price_to_tick(4993) == 4995   # tick 5


def test_tick_under_20000():
    assert risk.round_price_to_tick(15004) == 15000  # tick 10


def test_tick_under_200000():
    assert risk.round_price_to_tick(70250) == 70300  # tick 100


def test_tick_over_500000():
    assert risk.round_price_to_tick(523400) == 523000  # tick 1000


# ── 포지션 사이징 ───────────────────────────────────────
def test_calc_trade_basic():
    cfg = Config()
    tp = risk.calc_trade(entry=70200, sl=69200, balance=10_000_000, cfg=cfg)
    assert tp is not None
    assert tp.total_qty == 100
    assert (tp.qty1, tp.qty2, tp.qty3) == (33, 33, 34)
    assert tp.tp1_price == 71200
    assert tp.tp2_price == 72700
    assert tp.tp3_price == 74200
    assert tp.sl_price == 69200
    assert tp.risk_r == pytest.approx(1000.0)


def test_calc_trade_capped_by_balance():
    cfg = Config(risk_pct=1.0)  # 비현실적 큰 리스크 → 잔고 상한 적용
    tp = risk.calc_trade(entry=10000, sl=9000, balance=1_000_000, cfg=cfg)
    assert tp.total_qty == 100  # floor(1,000,000 / 10,000)


def test_calc_trade_qty_too_small_none():
    cfg = Config()
    tp = risk.calc_trade(entry=70200, sl=69200, balance=10_000, cfg=cfg)
    assert tp is None


def test_calc_trade_invalid_stop_none():
    cfg = Config()
    assert risk.calc_trade(entry=100, sl=100, balance=10_000_000, cfg=cfg) is None
    assert risk.calc_trade(entry=100, sl=110, balance=10_000_000, cfg=cfg) is None


# ── 손절 계산 ───────────────────────────────────────────
def test_compute_stop_atr():
    cfg = Config(sl_atr_mult=2.0)
    assert risk.compute_stop(entry=100, atr=2, cfg=cfg) == pytest.approx(96.0)


def test_compute_stop_uses_ob_when_lower():
    cfg = Config(sl_atr_mult=2.0)
    assert risk.compute_stop(entry=100, atr=2, cfg=cfg, ob_bot=95) == pytest.approx(95.0)


def test_compute_stop_keeps_atr_when_ob_higher():
    cfg = Config(sl_atr_mult=2.0)
    assert risk.compute_stop(entry=100, atr=2, cfg=cfg, ob_bot=97) == pytest.approx(96.0)


def test_compute_stop_clamped_to_limit_down():
    cfg = Config(sl_atr_mult=2.0, price_limit_pct=30.0)
    # atr_stop = 100 - 50*2 = 0 → 하한가(70)로 클램프
    assert risk.compute_stop(entry=100, atr=50, cfg=cfg, prev_close=100) == pytest.approx(70.0)


# ── 추적 SL ─────────────────────────────────────────────
def test_trailing_sl_initial():
    cfg = Config()
    assert risk.trailing_sl(entry=100, initial_sl=96, risk_r=4, tp_count=0, cfg=cfg) == 96


def test_trailing_sl_after_tp1():
    cfg = Config()
    # entry + 0.5R = 100 + 2 = 102
    assert risk.trailing_sl(entry=100, initial_sl=96, risk_r=4, tp_count=1, cfg=cfg) == 102


def test_trailing_sl_after_tp2():
    cfg = Config()
    # entry + 1.5R = 100 + 6 = 106
    assert risk.trailing_sl(entry=100, initial_sl=96, risk_r=4, tp_count=2, cfg=cfg) == 106
