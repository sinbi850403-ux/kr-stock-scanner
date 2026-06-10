"""scanner.py 테스트 — 종목필터·깔때기·멀티종목 스캔·캐싱."""
from datetime import datetime
import pytz
import pytest
from tests.conftest import make_df
from config import Config
import scanner
from strategy import Signal

KST = pytz.timezone("Asia/Seoul")
NOW = KST.localize(datetime(2026, 6, 9, 15, 45))


# ── 종목 필터 ───────────────────────────────────────────
def test_tradable_normal():
    assert scanner.is_tradable("005930", "삼성전자") is True


def test_tradable_preferred_excluded():
    assert scanner.is_tradable("005935", "삼성전자우") is False   # 끝자리 5


def test_tradable_etf_excluded():
    assert scanner.is_tradable("069500", "KODEX 200") is False


def test_tradable_bad_code():
    assert scanner.is_tradable("12345", "x") is False            # 6자리 아님


# ── 깔때기 후보 ─────────────────────────────────────────
class FakeClient:
    def __init__(self, ranks=None, candle_df=None):
        self._ranks = ranks or {}
        self._df = candle_df
        self.candle_calls = []

    def volume_rank(self, blng_cls="1", market="0000"):
        return self._ranks.get(("vol", blng_cls), [])

    def fluctuation_rank(self, market="0000"):
        return self._ranks.get(("flx",), [])

    def get_candles(self, code, period="D", count=100):
        self.candle_calls.append((code, period))
        return self._df if self._df is not None else make_df([100.0] * 100)

    def get_investor_flow(self, code):
        return (0.0, 0.0)


def test_build_candidates_dedup_and_filter():
    ranks = {
        ("vol", "1"): [("000100", "에이"), ("005935", "우선주")],
        ("vol", "3"): [("000100", "에이"), ("000200", "비")],   # 000100 중복
        ("flx",): [("000300", "씨"), ("069500", "KODEX 200")],
    }
    sc = scanner.Scanner(Config(), FakeClient(ranks))
    cand = sc.build_candidates()
    assert set(cand.keys()) == {"000100", "000200", "000300"}    # 우선주·ETF 제외, 중복 제거


# ── 스캔 ────────────────────────────────────────────────
def _patch_gen(monkeypatch, score_map):
    def fake_gen(code, d, w, m, cfg, prev_close=None, timestamp="",
                 inst_qty=0.0, frgn_qty=0.0):
        sc = score_map.get(code)
        if sc is None:
            return None
        return Signal(code, "long", 100, 95, sc, {}, 10, 1.5)
    monkeypatch.setattr(scanner.strategy, "generate_signal", fake_gen)


def test_scan_sorts_by_score(monkeypatch):
    ranks = {("vol", "1"): [("000100", "에이"), ("000200", "비"), ("000300", "씨")]}
    sc = scanner.Scanner(Config(), FakeClient(ranks))
    _patch_gen(monkeypatch, {"000100": 5, "000200": 6, "000300": None})
    sigs = sc.scan(now=NOW)
    assert [s.symbol for _, s in sigs] == ["000200", "000100"]   # 6점 먼저, None 제외


def test_scan_skips_symbols(monkeypatch):
    ranks = {("vol", "1"): [("000100", "에이"), ("000200", "비")]}
    sc = scanner.Scanner(Config(), FakeClient(ranks))
    _patch_gen(monkeypatch, {"000100": 5, "000200": 6})
    sigs = sc.scan(skip_symbols={"000200"}, now=NOW)
    assert [s.symbol for _, s in sigs] == ["000100"]


def test_scan_empty_candles_skipped(monkeypatch):
    import pandas as pd
    ranks = {("vol", "1"): [("000100", "에이")]}
    client = FakeClient(ranks, candle_df=pd.DataFrame())
    sc = scanner.Scanner(Config(), client)
    _patch_gen(monkeypatch, {"000100": 6})
    assert sc.scan(now=NOW) == []


def test_scan_logs_candidate_and_signal_count(monkeypatch, caplog):
    """스캔마다 후보 수·신호 수를 INFO 로그로 남겨 Railway에서 원인 파악 가능."""
    import logging
    ranks = {("vol", "1"): [("000100", "에이"), ("000200", "비"), ("000300", "씨")]}
    sc = scanner.Scanner(Config(), FakeClient(ranks))
    _patch_gen(monkeypatch, {"000100": 5, "000200": 6, "000300": None})
    with caplog.at_level(logging.INFO, logger="scanner"):
        sc.scan(now=NOW)
    assert "후보 3" in caplog.text      # 후보 수 로깅
    assert "신호 2" in caplog.text      # 신호 수 로깅


def test_scan_logs_zero_candidates(monkeypatch, caplog):
    """후보가 0개이면 경고 로그 (API 오류 가능성)."""
    import logging
    sc = scanner.Scanner(Config(), FakeClient({}))  # 빈 랭킹
    with caplog.at_level(logging.WARNING, logger="scanner"):
        sc.scan(now=NOW)
    assert "후보 0" in caplog.text


# ── 주/월봉 캐싱 ────────────────────────────────────────
def test_weekly_monthly_cached_same_day(monkeypatch):
    ranks = {("vol", "1"): [("000100", "에이")]}
    client = FakeClient(ranks)
    sc = scanner.Scanner(Config(), client)
    _patch_gen(monkeypatch, {"000100": 6})
    sc.scan(now=NOW)
    sc.scan(now=NOW)   # 같은 날 두 번째 스캔
    # 주봉/월봉은 하루 1회만 조회 (일봉은 매번)
    w_calls = [c for c in client.candle_calls if c == ("000100", "W")]
    m_calls = [c for c in client.candle_calls if c == ("000100", "M")]
    assert len(w_calls) == 1
    assert len(m_calls) == 1
