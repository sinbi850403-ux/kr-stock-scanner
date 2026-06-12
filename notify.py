"""
텔레그램 알림 — 포맷(순수) + 발송(I/O).
"""
import html
import logging
import requests

from config import Config

log = logging.getLogger(__name__)

_LAYER_ORDER = ["L1", "L2", "L3", "L4", "L5", "L6"]


def _esc(s) -> str:
    """텔레그램 parse_mode=HTML 깨짐 방지: 동적 문자열의 < > & 이스케이프."""
    return html.escape(str(s))


def _checks(layers: dict) -> str:
    return " ".join(f"{k}{'✅' if layers.get(k) else '❌'}" for k in _LAYER_ORDER)


def fmt_scan_signal(sig, name, provisional=True) -> str:
    tag = "<i>(장중 잠정)</i>" if provisional else "<b>(마감 확정)</b>"
    tier = f"🌟{sig.score}/6" if sig.score >= 6 else f"⭐{sig.score}/6"
    risk = sig.entry_price - sig.sl_price
    tp1 = sig.entry_price + risk * 1.0
    tp2 = sig.entry_price + risk * 2.5
    return (f"📈 <b>[{_esc(name)}] {_esc(sig.symbol)}</b> 매수신호 {tag}\n"
            f"등급: {tier} ({sig.score}/6) | 거래량 {sig.rvol:.1f}x\n"
            f"현재가: {sig.entry_price:,.0f}원\n"
            f"손절: {sig.sl_price:,.0f}원\n"
            f"목표1: {tp1:,.0f}원 (+{(tp1/sig.entry_price-1)*100:.1f}%) | "
            f"목표2: {tp2:,.0f}원 (+{(tp2/sig.entry_price-1)*100:.1f}%)\n"
            f"{_checks(sig.layers)}")


def fmt_entry(symbol, name, params) -> str:
    return (f"🟢 <b>진입 [{_esc(name)}] {_esc(symbol)}</b>\n"
            f"진입가: {params.entry_price:,.0f}원 × {params.total_qty}주\n"
            f"손절: {params.sl_price:,.0f}원\n"
            f"TP1/2/3: {params.tp1_price:,.0f} / {params.tp2_price:,.0f} / "
            f"{params.tp3_price:,.0f}\n"
            f"분할: {params.qty1}/{params.qty2}/{params.qty3}주")


def fmt_tp(n, symbol, qty, price, pnl_krw) -> str:
    return (f"📈 <b>TP{n} 체결 [{_esc(symbol)}]</b>\n"
            f"{qty}주 @ {price:,.0f}원 | 손익 {pnl_krw:+,.0f}원")


def fmt_sl(symbol, entry, sl, pnl_krw) -> str:
    return (f"🔴 <b>SL 청산 [{_esc(symbol)}]</b>\n"
            f"진입 {entry:,.0f} → 손절 {sl:,.0f}원 | 손익 {pnl_krw:+,.0f}원")


def fmt_counter(symbol, entry, price, reason) -> str:
    return (f"⚠️ <b>역신호 청산 [{_esc(symbol)}]</b>\n"
            f"진입 {entry:,.0f} → 청산 {price:,.0f}원\n"
            f"사유: {_esc(reason)}")


class Notifier:
    def __init__(self, cfg: Config, session=None):
        self.cfg = cfg
        self.session = session or requests

    def send(self, text: str) -> bool:
        try:
            resp = self.session.post(
                f"https://api.telegram.org/bot{self.cfg.telegram_token}/sendMessage",
                json={"chat_id": self.cfg.telegram_chat, "text": text,
                      "parse_mode": "HTML"},
                timeout=10)
            resp.raise_for_status()
            return True
        except requests.RequestException as e:
            # 4xx(잘못된 토큰/chat_id, HTML 파싱 실패 등)는 응답 본문에 실패 사유가 담김
            body = getattr(getattr(e, "response", None), "text", "") or ""
            if body:
                log.error("텔레그램 발송 실패: %s | 응답: %s", e, body)
            else:
                log.error("텔레그램 발송 실패: %s", e)
            return False

    def alert_scan_signal(self, sig, name, provisional=True):
        return self.send(fmt_scan_signal(sig, name, provisional))

    def alert_entry(self, symbol, name, params):
        return self.send(fmt_entry(symbol, name, params))

    def alert_tp(self, n, symbol, qty, price, pnl_krw):
        return self.send(fmt_tp(n, symbol, qty, price, pnl_krw))

    def alert_sl(self, symbol, entry, sl, pnl_krw):
        return self.send(fmt_sl(symbol, entry, sl, pnl_krw))

    def alert_counter(self, symbol, entry, price, reason):
        return self.send(fmt_counter(symbol, entry, price, reason))

    def alert_watch_tp(self, n, symbol, name, entry, cur, pnl_pct, tp2=None):
        next_line = f"\n다음 목표: {tp2:,.0f}원" if tp2 else "\n🎯 전량 청산 타이밍"
        sl_note = "\n✅ 손절선 → 진입가로 상향 (본전 보호)" if n == 1 else ""
        return self.send(
            f"{'📈' if n == 1 else '🏆'} <b>목표{n} 도달 [{_esc(symbol)}]</b>\n"
            f"종목: {_esc(name)}\n"
            f"진입 {entry:,.0f}원 → 현재 {cur:,.0f}원 ({pnl_pct:+.1f}%)"
            f"{sl_note}{next_line}"
        )

    def alert_watch_sl(self, symbol, name, entry, sl, cur, pnl_pct):
        return self.send(
            f"🔴 <b>손절 도달 [{_esc(symbol)}]</b>\n"
            f"종목: {_esc(name)}\n"
            f"진입 {entry:,.0f}원 → 손절 {sl:,.0f}원 | 현재 {cur:,.0f}원 ({pnl_pct:+.1f}%)"
        )

    def alert_error(self, msg):
        return self.send(f"🛑 <b>에러</b>\n{_esc(msg)}")
