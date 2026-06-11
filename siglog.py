"""
신호 기록 모듈 — JSONL 파일 + logging.

forward-test 표본 축적용. 파일 쓰기 실패해도 봇 중단 금지.
"""
import json
import logging
from datetime import datetime
from typing import Optional

from strategy import Signal

log = logging.getLogger(__name__)


def log_signal(sig: Signal, name: str, phase: str, now: datetime,
               path: str = "signals_log.jsonl") -> None:
    """신호를 JSONL + logging으로 기록.

    Args:
        sig: Signal 객체
        name: 종목명 (삼성전자 등)
        phase: "intraday" / "postclose" / "evening"
        now: 현재시각 (timezone-aware datetime)
        path: JSONL 파일 경로 (None이면 파일 미기록, logging만)
    """
    ts = now.isoformat()
    date = now.strftime("%Y%m%d")

    record = {
        "ts": ts,
        "date": date,
        "phase": phase,
        "symbol": sig.symbol,
        "name": name,
        "score": sig.score,
        "layers": sig.layers,
        "entry": sig.entry_price,
        "sl": sig.sl_price,
        "rvol": sig.rvol,
        "atr": sig.atr,
    }

    # logging 출력 (Railway 로그에 영속)
    try:
        log.info("SIGLOG %s", json.dumps(record, ensure_ascii=False))
    except Exception as e:                              # noqa: BLE001
        log.warning("SIGLOG 직렬화 실패: %s", e)

    # 파일 쓰기 (실패해도 무시)
    if path:
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as e:
            log.error("SIGLOG 파일 쓰기 실패 (%s): %s", path, e)
