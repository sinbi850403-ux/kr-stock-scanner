"""
멀티종목 스캐너 — 깔때기(순위) → 후보 → 일/주/월봉 → 6레이어 신호.

부하 최적화: 주봉/월봉은 하루 1회만 조회(캐싱). 일봉은 매 스캔 조회.
"""
import logging
from datetime import datetime

import pytz

import strategy
from config import Config

log = logging.getLogger(__name__)
KST = pytz.timezone("Asia/Seoul")

_BAD_NAME = ["우", "스팩", "ETN", "ETF", "리츠", "인버스", "레버리지",
             "KODEX", "TIGER", "ARIRANG", "KBSTAR", "HANARO", "PLUS",
             "ACE", "SOL", "RISE", "TIMEFOLIO"]


def is_tradable(code: str, name: str) -> bool:
    """우선주/ETF/ETN/스팩/리츠 등 제외 (휴리스틱)."""
    if not code or len(code) != 6 or not code.isdigit():
        return False
    if code[-1] != "0":          # 우선주: 끝자리 0 아님
        return False
    return not any(b in (name or "") for b in _BAD_NAME)


class Scanner:
    def __init__(self, cfg: Config, client):
        self.cfg = cfg
        self.client = client
        self._htf_cache = {}      # {(code, period): (datestr, df)}

    def build_candidates(self) -> dict:
        cand = {}
        try:
            sources = (self.client.volume_rank("1"),    # 거래증가율
                       self.client.volume_rank("3"),    # 거래대금
                       self.client.fluctuation_rank())  # 상승률
            for rows in sources:
                for code, name in rows:
                    if is_tradable(code, name):
                        cand[code] = name
        except Exception as e:                          # noqa: BLE001
            log.error("깔때기 후보 생성 오류: %s", e)
        return cand

    def _cached_htf(self, code, period, count, datestr):
        key = (code, period)
        hit = self._htf_cache.get(key)
        if hit and hit[0] == datestr:
            return hit[1]
        df = self.client.get_candles(code, period, count)
        self._htf_cache[key] = (datestr, df)
        return df

    def _get_mtf(self, code, datestr):
        df_d = self.client.get_candles(code, "D", self.cfg.daily_count)
        df_w = self._cached_htf(code, "W", self.cfg.weekly_count, datestr)
        df_m = self._cached_htf(code, "M", self.cfg.monthly_count, datestr)
        return df_d, df_w, df_m

    def scan(self, skip_symbols=None, now=None):
        """후보 스캔 → [(name, Signal)] 점수 내림차순."""
        skip_symbols = skip_symbols or set()
        now = now or datetime.now(KST)
        datestr = now.strftime("%Y%m%d")

        cand = self.build_candidates()
        signals = []
        for code, name in cand.items():
            if code in skip_symbols:
                continue
            try:
                df_d, df_w, df_m = self._get_mtf(code, datestr)
                if df_d is None or df_d.empty:
                    continue
                prev_close = float(df_d["close"].iloc[-2]) if len(df_d) >= 2 else None
                inst_qty, frgn_qty = 0.0, 0.0
                try:
                    inst_qty, frgn_qty = self.client.get_investor_flow(code)
                except Exception as e:  # noqa: BLE001
                    log.debug("수급조회 실패 %s: %s", code, e)
                sig = strategy.generate_signal(code, df_d, df_w, df_m, self.cfg,
                                               prev_close=prev_close,
                                               inst_qty=inst_qty, frgn_qty=frgn_qty)
                if sig:
                    signals.append((name, sig))
            except Exception as e:                       # noqa: BLE001
                log.error("스캔 오류 %s: %s", code, e)

        signals.sort(key=lambda x: x[1].score, reverse=True)
        return signals
