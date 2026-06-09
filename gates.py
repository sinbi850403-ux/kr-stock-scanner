"""
안전 게이트 (순수) — 진입 전 검증.

하드 베토(차단): 갭/상하한가/거래시간/거래횟수/포지션수/중복.
경고만(통과): VI.
시각(now)은 KST tz-aware datetime을 외부에서 주입(테스트 용이).
"""
from datetime import timedelta
from typing import Optional, Tuple, List

from config import Config


def check_gap_filter(open_price: float, prev_close: float, cfg: Config) -> Tuple[bool, str]:
    if not prev_close:
        return True, "전일종가 없음(스킵)"
    gap = abs((open_price - prev_close) / prev_close) * 100.0
    if gap > cfg.gap_filter_pct:
        return False, f"시가갭 {gap:.1f}% > {cfg.gap_filter_pct}% — 추격 차단"
    return True, "갭 OK"


def check_limit_up_down(current_price: float, prev_close: float, cfg: Config) -> Tuple[bool, str]:
    if not prev_close:
        return True, "전일종가 없음(스킵)"
    move = (current_price - prev_close) / prev_close * 100.0
    if move > cfg.limit_veto_pct:
        return False, f"상한가 근접 +{move:.1f}% — 진입 금지"
    if move < -cfg.limit_veto_pct:
        return False, f"하한가 근접 {move:.1f}% — 진입 금지"
    return True, "상/하한 OK"


def check_vi_limit(current_price: float, prev_close: float, cfg: Config) -> Tuple[bool, str]:
    """경고만 — 항상 통과(True)."""
    if not prev_close:
        return True, ""
    move = (current_price - prev_close) / prev_close * 100.0
    if abs(move) >= cfg.vi_warn_pct:
        return True, f"⚠️ VI 구간 {move:+.1f}% — 신호 신뢰도 주의"
    return True, "VI OK"


def check_trading_hours(now, cfg: Config, holidays: Optional[set] = None) -> Tuple[bool, str]:
    if now.weekday() >= 5:
        return False, "주말 — 거래 없음"
    if holidays and now.strftime("%Y%m%d") in holidays:
        return False, "공휴일 — 거래 없음"
    hhmm = now.strftime("%H%M")
    if hhmm < cfg.entry_start:
        return False, f"{hhmm} — 진입 시작({cfg.entry_start}) 전"
    if hhmm >= cfg.entry_end:
        return False, f"{hhmm} — 진입 종료({cfg.entry_end}) 후"
    return True, "거래시간 OK"


def check_daily_trade_limit(count: int, cfg: Config) -> Tuple[bool, str]:
    if count >= cfg.max_daily_trades:
        return False, f"일일 거래횟수 초과 ({count}/{cfg.max_daily_trades})"
    return True, f"거래횟수 OK ({count}/{cfg.max_daily_trades})"


def check_position_limit(current_positions: int, cfg: Config) -> Tuple[bool, str]:
    if current_positions >= cfg.max_positions:
        return False, f"포지션 수 초과 ({current_positions}/{cfg.max_positions})"
    return True, f"포지션 OK ({current_positions}/{cfg.max_positions})"


def check_duplicate_entry(symbol: str, daily_log: List[dict], now, cfg: Config) -> Tuple[bool, str]:
    for trade in daily_log:
        if trade.get("symbol") == symbol:
            t = trade.get("time")
            if t is not None and (now - t) < timedelta(hours=1):
                return False, f"{symbol} 1시간 내 재진입 차단"
    return True, "중복 OK"


def validate_signal(*, open_price, prev_close, current_price, now,
                    daily_trade_count, current_positions, symbol, daily_log,
                    cfg: Config, holidays: Optional[set] = None) -> Tuple[bool, str]:
    checks = [
        check_trading_hours(now, cfg, holidays),
        check_gap_filter(open_price, prev_close, cfg),
        check_limit_up_down(current_price, prev_close, cfg),
        check_daily_trade_limit(daily_trade_count, cfg),
        check_position_limit(current_positions, cfg),
        check_duplicate_entry(symbol, daily_log, now, cfg),
    ]
    for ok, reason in checks:
        if not ok:
            return False, reason
    return True, "모든 게이트 통과"
