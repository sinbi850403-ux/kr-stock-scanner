"""confluence.py 테스트 — L1~L7 레이어 + 합산."""
import pytest
from tests.conftest import make_df
from config import Config
import confluence as cf


# ── 레이어 헬퍼 (직접값) ────────────────────────────────
def test_l1_pass():
    assert cf.l1_trend(e5=110, e20=105, e60=100, close=112) is True


def test_l1_fail_not_stacked():
    assert cf.l1_trend(e5=100, e20=105, e60=110, close=112) is False


def test_l1_fail_below_slow():
    assert cf.l1_trend(e5=110, e20=105, e60=100, close=99) is False


def test_l2_all_aligned():
    assert cf.l2_mtf(d=(105, 100, 1.5), w=(50, 48), m=(20, 19)) is True


def test_l2_daily_macd_negative():
    assert cf.l2_mtf(d=(105, 100, -0.5), w=(50, 48), m=(20, 19)) is False


def test_l2_weekly_misaligned():
    assert cf.l2_mtf(d=(105, 100, 1.5), w=(47, 48), m=(20, 19)) is False


def test_l2_monthly_misaligned():
    assert cf.l2_mtf(d=(105, 100, 1.5), w=(50, 48), m=(18, 19)) is False


def test_l4_rsi_crossover():
    assert cf.l4_momentum(rsi_prev=44, rsi_now=46, rsi_buy=45,
                          macd_prev=0, macd_now=0, sig_prev=0, sig_now=0,
                          hist_now=0, hist_prev=0) is True


def test_l4_macd_crossover():
    assert cf.l4_momentum(rsi_prev=50, rsi_now=51, rsi_buy=45,
                          macd_prev=-0.1, macd_now=0.2, sig_prev=0.0, sig_now=0.1,
                          hist_now=0.1, hist_prev=-0.1) is True


def test_l4_macd_cross_but_negative_fails():
    # 골든크로스지만 macd<0 → 실패
    assert cf.l4_momentum(rsi_prev=50, rsi_now=51, rsi_buy=45,
                          macd_prev=-0.5, macd_now=-0.2, sig_prev=-0.6, sig_now=-0.3,
                          hist_now=0.1, hist_prev=-0.1) is False


def test_l4_none():
    assert cf.l4_momentum(rsi_prev=50, rsi_now=51, rsi_buy=45,
                          macd_prev=0.5, macd_now=0.6, sig_prev=0.4, sig_now=0.5,
                          hist_now=0.1, hist_prev=0.1) is False


def test_l5_two_of_three():
    # volSurge + obvRising = 2 (vwap 아래) → 통과
    assert cf.l5_volume(rvol_now=2.0, obv_now=10, obv_prev=5, close=100, vwap=101,
                        open_=99, vol_surge=1.7, distribution=False) is True


def test_l5_only_one_fails():
    assert cf.l5_volume(rvol_now=1.0, obv_now=10, obv_prev=5, close=100, vwap=101,
                        open_=99, vol_surge=1.7, distribution=False) is False


def test_l5_distribution_veto():
    assert cf.l5_volume(rvol_now=2.0, obv_now=10, obv_prev=5, close=102, vwap=99,
                        open_=99, vol_surge=1.7, distribution=True) is False


def test_l5_bearish_candle_fails():
    assert cf.l5_volume(rvol_now=2.0, obv_now=10, obv_prev=5, close=98, vwap=99,
                        open_=100, vol_surge=1.7, distribution=False) is False


def test_is_distribution_true():
    assert cf.is_distribution(rvol_now=2.5, vol_strong=2.0, body=0.1, bar_range=10,
                              close=100, hi10=100) is True


def test_is_distribution_big_body_false():
    assert cf.is_distribution(rvol_now=2.5, vol_strong=2.0, body=8, bar_range=10,
                              close=100, hi10=100) is False


# ── full() 통합 ─────────────────────────────────────────
def _rising(n, start=100.0, step=1.0):
    closes = [start + i * step for i in range(n)]
    return make_df(closes)


def test_full_insufficient_returns_none():
    cfg = Config()
    assert cf.full(_rising(30), _rising(60), _rising(24), cfg) is None


def test_full_clean_uptrend_layers():
    cfg = Config()
    res = cf.full(_rising(100), _rising(60), _rising(30), cfg)
    assert res is not None
    # 깨끗한 단조 상승: L1(정배열)·L2(MTF)·L5(OBV+VWAP) 켜짐
    assert res.l1 is True
    assert res.l2 is True
    assert res.l5 is True
    # 피봇·크로스오버 없음: L3·L4·L6 꺼짐
    assert res.l3 is False
    assert res.l4 is False
    assert res.l6 is False
    assert res.score == 3


def test_full_missing_weekly_fails_l2():
    cfg = Config()
    res = cf.full(_rising(100), None, _rising(30), cfg)
    assert res is not None
    assert res.l2 is False


# ── L7 수급 ────────────────────────────────────────────────
def test_l7_inst_positive():
    assert cf.l7_supply(inst_qty=1000, frgn_qty=0) is True


def test_l7_frgn_positive():
    assert cf.l7_supply(inst_qty=0, frgn_qty=500) is True


def test_l7_both_zero():
    assert cf.l7_supply(inst_qty=0, frgn_qty=0) is False


def test_l7_net_selling():
    assert cf.l7_supply(inst_qty=-5000, frgn_qty=-200) is False


def test_full_l7_via_params():
    cfg = Config()
    res_no  = cf.full(_rising(100), _rising(60), _rising(30), cfg, inst_qty=0, frgn_qty=0)
    res_yes = cf.full(_rising(100), _rising(60), _rising(30), cfg, inst_qty=1000, frgn_qty=0)
    assert res_no is not None and res_yes is not None
    assert res_yes.l7 is True
    assert res_no.l7  is False
    assert res_yes.score == res_no.score + 1


def test_full_result_fields():
    cfg = Config()
    res = cf.full(_rising(100), _rising(60), _rising(30), cfg)
    assert res.price == pytest.approx(199.0)
    assert res.atr > 0
    assert 0 <= res.score <= 7
