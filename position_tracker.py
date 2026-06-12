"""
포지션 추적기 — 알림 발생 종목의 현재가를 감시해 매도 타이밍 알림.

흐름:
  신호 알림 → add()          : entry/sl/tp1/tp2 저장
  매 사이클 → check()        : 현재가 조회 → TP/SL 판정
  TP1 도달                   : 절반 매도 권고 + SL을 진입가로 상향
  TP2 도달 / SL 이탈         : 전량 청산 권고 + 포지션 제거
  N일 경과 (MAX_DAYS)        : 자동 만료 제거
"""
import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import List, Optional

import pytz

log = logging.getLogger(__name__)
KST = pytz.timezone("Asia/Seoul")
MAX_DAYS = 10   # 신호 후 N 영업일 이상 경과 시 자동 만료


@dataclass
class WatchedPos:
    symbol: str
    name: str
    entry: float
    sl: float
    tp1: float
    tp2: float
    date: str           # YYYYMMDD — 신호 발생일
    sl_moved: bool = False   # TP1 도달 후 SL을 진입가로 올린 상태


class PositionTracker:
    def __init__(self, path: str = "watched_positions.json"):
        self.path = path
        self.positions: List[WatchedPos] = []
        self._load()

    # ── 영속화 ──
    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump([asdict(p) for p in self.positions], f,
                          ensure_ascii=False, indent=2)
        except OSError as e:
            log.error("포지션 추적 저장 실패: %s", e)

    def _load(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                self.positions = [WatchedPos(**d) for d in json.load(f)]
        except (OSError, json.JSONDecodeError, TypeError) as e:
            log.error("포지션 추적 로드 실패: %s", e)
            self.positions = []

    # ── 포지션 추가 ──
    def add(self, symbol: str, name: str, entry: float, sl: float,
            tp1: float, tp2: float, now: Optional[datetime] = None):
        now = now or datetime.now(KST)
        date = now.strftime("%Y%m%d")
        # 같은 종목이 이미 추적 중이면 스킵
        if any(p.symbol == symbol for p in self.positions):
            return
        pos = WatchedPos(symbol=symbol, name=name, entry=entry,
                         sl=sl, tp1=tp1, tp2=tp2, date=date)
        self.positions.append(pos)
        self._save()
        log.info("추적 등록 %s entry=%.0f sl=%.0f tp1=%.0f tp2=%.0f",
                 symbol, entry, sl, tp1, tp2)

    # ── 만료 제거 ──
    def _is_expired(self, pos: WatchedPos, today: str) -> bool:
        try:
            d0 = datetime.strptime(pos.date, "%Y%m%d")
            d1 = datetime.strptime(today, "%Y%m%d")
            return (d1 - d0).days >= MAX_DAYS
        except ValueError:
            return False

    # ── 메인 체크 ──
    def check(self, client, notifier, now: Optional[datetime] = None):
        """현재가 조회 후 SL/TP 판정. 알림 필요 시 notifier 호출."""
        if not self.positions:
            return
        now = now or datetime.now(KST)
        today = now.strftime("%Y%m%d")
        remove = []

        for pos in list(self.positions):
            if self._is_expired(pos, today):
                log.info("추적 만료 %s (%s일 경과)", pos.symbol, MAX_DAYS)
                remove.append(pos)
                continue

            try:
                pinfo = client.get_current_price(pos.symbol)
                cur = pinfo["price"]
            except Exception as e:                        # noqa: BLE001
                log.warning("추적 현재가 조회 실패 %s: %s", pos.symbol, e)
                continue

            cur_sl = pos.entry if pos.sl_moved else pos.sl

            # SL 이탈
            if cur <= cur_sl:
                pnl_pct = (cur - pos.entry) / pos.entry * 100
                notifier.alert_watch_sl(pos.symbol, pos.name,
                                        pos.entry, cur_sl, cur, pnl_pct)
                remove.append(pos)
                continue

            # TP2 도달
            if cur >= pos.tp2:
                pnl_pct = (cur - pos.entry) / pos.entry * 100
                notifier.alert_watch_tp(2, pos.symbol, pos.name,
                                        pos.entry, cur, pnl_pct)
                remove.append(pos)
                continue

            # TP1 도달 (최초 1회)
            if not pos.sl_moved and cur >= pos.tp1:
                pnl_pct = (cur - pos.entry) / pos.entry * 100
                notifier.alert_watch_tp(1, pos.symbol, pos.name,
                                        pos.entry, cur, pnl_pct,
                                        tp2=pos.tp2)
                pos.sl_moved = True  # SL → 진입가로 이동
                self._save()

        for p in remove:
            self.positions.remove(p)
        if remove:
            self._save()
