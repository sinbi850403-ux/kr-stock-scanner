"""PositionTracker 단위 테스트."""
import json
import os
import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch, call
import pytz

from position_tracker import PositionTracker, WatchedPos

KST = pytz.timezone("Asia/Seoul")


def make_tracker(tmp_path):
    path = str(tmp_path / "watch.json")
    return PositionTracker(path=path)


def make_now(date="20260612"):
    return datetime.strptime(date, "%Y%m%d").replace(tzinfo=KST)


# ── add ──

class TestAdd:
    def test_add_registers_position(self, tmp_path):
        t = make_tracker(tmp_path)
        t.add("005930", "삼성전자", 70000, 65000, 74000, 77500, make_now())
        assert len(t.positions) == 1
        p = t.positions[0]
        assert p.symbol == "005930"
        assert p.entry == 70000
        assert p.tp1 == 74000
        assert p.tp2 == 77500
        assert p.sl_moved is False

    def test_add_skips_duplicate_symbol(self, tmp_path):
        t = make_tracker(tmp_path)
        now = make_now()
        t.add("005930", "삼성전자", 70000, 65000, 74000, 77500, now)
        t.add("005930", "삼성전자", 71000, 66000, 75000, 78500, now)
        assert len(t.positions) == 1
        assert t.positions[0].entry == 70000  # 첫 번째 유지

    def test_add_saves_to_file(self, tmp_path):
        t = make_tracker(tmp_path)
        t.add("005930", "삼성전자", 70000, 65000, 74000, 77500, make_now())
        data = json.loads(open(str(tmp_path / "watch.json"), encoding="utf-8").read())
        assert len(data) == 1
        assert data[0]["symbol"] == "005930"


# ── 영속화 ──

class TestPersistence:
    def test_load_restores_positions(self, tmp_path):
        t1 = make_tracker(tmp_path)
        t1.add("005930", "삼성전자", 70000, 65000, 74000, 77500, make_now())
        # 새 인스턴스에서 로드
        t2 = PositionTracker(path=str(tmp_path / "watch.json"))
        assert len(t2.positions) == 1
        assert t2.positions[0].symbol == "005930"

    def test_load_missing_file_is_safe(self, tmp_path):
        t = PositionTracker(path=str(tmp_path / "nonexistent.json"))
        assert t.positions == []


# ── check: SL 이탈 ──

class TestCheckSL:
    def test_sl_hit_sends_alert_and_removes(self, tmp_path):
        t = make_tracker(tmp_path)
        now = make_now()
        t.add("005930", "삼성전자", 70000, 65000, 74000, 77500, now)

        client = MagicMock()
        client.get_current_price.return_value = {"price": 64000}  # SL 이탈
        notifier = MagicMock()

        t.check(client, notifier, now)

        notifier.alert_watch_sl.assert_called_once()
        args = notifier.alert_watch_sl.call_args[0]
        assert args[0] == "005930"
        assert len(t.positions) == 0

    def test_sl_after_tp1_uses_entry_as_sl(self, tmp_path):
        """TP1 도달 후 SL이 진입가로 올라간 상태에서 진입가 이하 시 손절."""
        t = make_tracker(tmp_path)
        now = make_now()
        t.add("005930", "삼성전자", 70000, 65000, 74000, 77500, now)
        t.positions[0].sl_moved = True  # TP1 이미 도달한 상태

        client = MagicMock()
        client.get_current_price.return_value = {"price": 69500}  # 진입가 이하
        notifier = MagicMock()

        t.check(client, notifier, now)

        notifier.alert_watch_sl.assert_called_once()
        assert len(t.positions) == 0


# ── check: TP1 도달 ──

class TestCheckTP1:
    def test_tp1_hit_sends_alert_and_moves_sl(self, tmp_path):
        t = make_tracker(tmp_path)
        now = make_now()
        t.add("005930", "삼성전자", 70000, 65000, 74000, 77500, now)

        client = MagicMock()
        client.get_current_price.return_value = {"price": 74500}  # TP1 도달
        notifier = MagicMock()

        t.check(client, notifier, now)

        notifier.alert_watch_tp.assert_called_once()
        args = notifier.alert_watch_tp.call_args[0]
        assert args[0] == 1  # n=1
        assert t.positions[0].sl_moved is True  # SL 상향됨
        assert len(t.positions) == 1  # 아직 포지션 유지

    def test_tp1_not_fired_twice(self, tmp_path):
        t = make_tracker(tmp_path)
        now = make_now()
        t.add("005930", "삼성전자", 70000, 65000, 74000, 77500, now)
        t.positions[0].sl_moved = True  # 이미 TP1 처리됨

        client = MagicMock()
        client.get_current_price.return_value = {"price": 75000}  # TP1 이상이지만 TP2 미달
        notifier = MagicMock()

        t.check(client, notifier, now)

        notifier.alert_watch_tp.assert_not_called()


# ── check: TP2 도달 ──

class TestCheckTP2:
    def test_tp2_hit_sends_alert_and_removes(self, tmp_path):
        t = make_tracker(tmp_path)
        now = make_now()
        t.add("005930", "삼성전자", 70000, 65000, 74000, 77500, now)
        t.positions[0].sl_moved = True

        client = MagicMock()
        client.get_current_price.return_value = {"price": 78000}  # TP2 도달
        notifier = MagicMock()

        t.check(client, notifier, now)

        notifier.alert_watch_tp.assert_called_once()
        args = notifier.alert_watch_tp.call_args[0]
        assert args[0] == 2  # n=2
        assert len(t.positions) == 0

    def test_tp2_hit_directly_without_tp1(self, tmp_path):
        """갭상승으로 TP1 건너뛰고 TP2 직접 도달."""
        t = make_tracker(tmp_path)
        now = make_now()
        t.add("005930", "삼성전자", 70000, 65000, 74000, 77500, now)

        client = MagicMock()
        client.get_current_price.return_value = {"price": 80000}  # TP2 초과
        notifier = MagicMock()

        t.check(client, notifier, now)

        notifier.alert_watch_tp.assert_called_once()
        assert notifier.alert_watch_tp.call_args[0][0] == 2
        assert len(t.positions) == 0


# ── 만료 ──

class TestExpiry:
    def test_expired_position_removed(self, tmp_path):
        t = make_tracker(tmp_path)
        # 11일 전 신호
        t.add("005930", "삼성전자", 70000, 65000, 74000, 77500,
              make_now("20260601"))

        client = MagicMock()
        client.get_current_price.return_value = {"price": 72000}
        notifier = MagicMock()

        t.check(client, notifier, make_now("20260612"))

        assert len(t.positions) == 0
        notifier.alert_watch_sl.assert_not_called()
        notifier.alert_watch_tp.assert_not_called()

    def test_not_expired_within_max_days(self, tmp_path):
        t = make_tracker(tmp_path)
        t.add("005930", "삼성전자", 70000, 65000, 74000, 77500,
              make_now("20260610"))  # 2일 전

        client = MagicMock()
        client.get_current_price.return_value = {"price": 72000}  # 홀딩
        notifier = MagicMock()

        t.check(client, notifier, make_now("20260612"))

        assert len(t.positions) == 1


# ── API 에러 내성 ──

class TestErrorTolerance:
    def test_api_error_skips_position(self, tmp_path):
        t = make_tracker(tmp_path)
        now = make_now()
        t.add("005930", "삼성전자", 70000, 65000, 74000, 77500, now)

        client = MagicMock()
        client.get_current_price.side_effect = Exception("API 오류")
        notifier = MagicMock()

        t.check(client, notifier, now)  # 예외 없이 통과

        assert len(t.positions) == 1  # 포지션 유지
        notifier.alert_watch_sl.assert_not_called()
        notifier.alert_watch_tp.assert_not_called()
