"""siglog.py 테스트 — 신호 기록(JSONL + logging)."""
import json
import logging
import tempfile
from pathlib import Path
from datetime import datetime

import pytz
import pytest

import siglog
from strategy import Signal

KST = pytz.timezone("Asia/Seoul")


def _dt(h, mi, d=9):
    return KST.localize(datetime(2026, 6, d, h, mi))


def _signal(symbol="005930", score=5):
    return Signal(
        symbol=symbol, direction="long", entry_price=70200,
        sl_price=69200, score=score,
        layers={"L1": True, "L2": True, "L3": True, "L4": True, "L5": True, "L6": False},
        atr=500, rvol=1.8, prev_close=69000
    )


# ── 파일 쓰기 ──────────────────────────────────────────
def test_log_signal_writes_jsonl(tmp_path):
    """신호를 JSONL로 파일에 기록."""
    log_file = tmp_path / "signals.jsonl"
    sig = _signal()
    siglog.log_signal(sig, "삼성전자", "intraday", _dt(10, 30), path=str(log_file))

    assert log_file.exists()
    with open(log_file, encoding="utf-8") as f:
        lines = f.readlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["symbol"] == "005930"
    assert record["phase"] == "intraday"
    assert record["name"] == "삼성전자"
    assert record["score"] == 5


def test_log_signal_multiple_appends(tmp_path):
    """여러 신호를 append."""
    log_file = tmp_path / "signals.jsonl"
    siglog.log_signal(_signal(symbol="000100"), "현대차", "intraday", _dt(10, 30), path=str(log_file))
    siglog.log_signal(_signal(symbol="005930"), "삼성", "postclose", _dt(15, 45), path=str(log_file))

    with open(log_file, encoding="utf-8") as f:
        records = [json.loads(line) for line in f]
    assert len(records) == 2
    assert records[0]["symbol"] == "000100"
    assert records[1]["symbol"] == "005930"


def test_log_signal_has_required_fields(tmp_path):
    """필드 존재 확인."""
    log_file = tmp_path / "signals.jsonl"
    sig = _signal()
    siglog.log_signal(sig, "삼성전자", "evening", _dt(17, 0), path=str(log_file))

    with open(log_file, encoding="utf-8") as f:
        record = json.loads(f.readline())

    # 필드 확인
    assert "ts" in record
    assert "date" in record
    assert "phase" in record
    assert "symbol" in record
    assert "name" in record
    assert "score" in record
    assert "layers" in record
    assert "entry" in record
    assert "sl" in record
    assert "rvol" in record
    assert "atr" in record


def test_log_signal_file_write_failure_logged_not_raised(tmp_path, caplog):
    """파일 쓰기 실패 시 예외 전파 안 하고 로그만 남김."""
    bad_path = "/nonexistent/dir/signals.jsonl"
    sig = _signal()

    with caplog.at_level(logging.ERROR):
        siglog.log_signal(sig, "삼성", "intraday", _dt(10, 30), path=bad_path)

    # 예외 없이 함수 복귀
    assert any("SIGLOG" in r.message and "실패" in r.message for r in caplog.records)


# ── logging 출력 ──────────────────────────────────────────
def test_log_signal_emits_logging_line(caplog):
    """logging으로도 SIGLOG 한 줄 출력."""
    sig = _signal()
    with caplog.at_level(logging.INFO):
        # tmp_path 사용 안 하고 로깅만 테스트
        siglog.log_signal(sig, "삼성", "intraday", _dt(10, 30), path=None)

    assert any("SIGLOG" in r.message for r in caplog.records)


def test_log_signal_logging_includes_json(caplog):
    """logging에 JSON 형식으로 출력."""
    sig = _signal(symbol="000500", score=6)
    with caplog.at_level(logging.INFO):
        siglog.log_signal(sig, "SK하이닉스", "postclose", _dt(15, 45), path=None)

    siglog_records = [r for r in caplog.records if "SIGLOG" in r.message]
    assert len(siglog_records) > 0
    # JSON이 포함되어 있는지 확인 (유효한 JSON 파싱 가능)
    msg = siglog_records[0].message
    try:
        json_part = msg.split(" ", 1)[1]  # "SIGLOG {json}"
        parsed = json.loads(json_part)
        assert parsed["symbol"] == "000500"
    except (IndexError, json.JSONDecodeError):
        pytest.fail(f"Invalid JSON in log: {msg}")


# ── 필드 값 검증 ────────────────────────────────────────
def test_log_signal_fields_correct_types(tmp_path):
    """필드 타입 확인."""
    log_file = tmp_path / "signals.jsonl"
    sig = _signal()
    now = _dt(10, 30)
    siglog.log_signal(sig, "삼성", "intraday", now, path=str(log_file))

    with open(log_file, encoding="utf-8") as f:
        record = json.loads(f.readline())

    assert isinstance(record["ts"], str)  # 타임스탐프
    assert isinstance(record["date"], str)  # YYYYMMDD
    assert isinstance(record["phase"], str)
    assert isinstance(record["symbol"], str)
    assert isinstance(record["name"], str)
    assert isinstance(record["score"], int)
    assert isinstance(record["entry"], (int, float))
    assert isinstance(record["sl"], (int, float))
    assert isinstance(record["rvol"], (int, float))


def test_log_signal_date_format(tmp_path):
    """date 필드 YYYYMMDD 형식."""
    log_file = tmp_path / "signals.jsonl"
    siglog.log_signal(_signal(), "삼성", "intraday", _dt(10, 30, d=15), path=str(log_file))

    with open(log_file, encoding="utf-8") as f:
        record = json.loads(f.readline())

    assert record["date"] == "20260615"


def test_log_signal_layers_dict(tmp_path):
    """layers 필드가 dict."""
    log_file = tmp_path / "signals.jsonl"
    siglog.log_signal(_signal(), "삼성", "intraday", _dt(10, 30), path=str(log_file))

    with open(log_file, encoding="utf-8") as f:
        record = json.loads(f.readline())

    assert isinstance(record["layers"], dict)
    assert "L1" in record["layers"]
    assert record["layers"]["L1"] is True
