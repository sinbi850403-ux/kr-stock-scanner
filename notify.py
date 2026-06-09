"""
텔레그램 알림 — 포맷(순수) + 발송(I/O).
"""
import logging
import requests

from config import Config

log = logging.getLogger(__name__)

_LAYER_ORDER = ["L1", "L2", "L3", "L4", "L5", "L6"]


def _checks(layers: dict) -> str:
    return " ".join(f"{k}{'✅' if layers.get(k) else '❌'}" for k in _LAYER_ORDER)


def fmt_scan_signal(sig, name, provisional=True) -> str:
    tag = "<i>(장중 잠정)</i>" if provisional else "<b>(마감 확정)</b>"
    tier = "🔥FULL" if sig.score >= 6 else f"⭐{sig.score}/6"
    return (f"📈 <b>[{name}] {sig.symbol}</b> 매수신호 {tag}\n"
            f"등급: {tier} ({sig.score}/6) | 거래량 {sig.rvol}x\n"
            f"현재가: {sig.entry_price:,.0f}원\n"
            f"손절: {sig.sl_price:,.0f}원\n"
            f"{_checks(sig.layers)}")


def fmt_entry(symbol, name, params) -> str:
    return (f"🟢 <b>진입 [{name}] {symbol}</b>\n"
            f"진입가: {params.entry_price:,.0f}원 × {params.total_qty}주\n"
            f"손절: {params.sl_price:,.0f}원\n"
            f"TP1/2/3: {params.tp1_price:,.0f} / {params.tp2_price:,.0f} / "
            f"{params.tp3_price:,.0f}\n"
            f"분할: {params.qty1}/{params.qty2}/{params.qty3}주")


def fmt_tp(n, symbol, qty, price, pnl_krw) -> str:
    return (f"📈 <b>TP{n} 체결 [{symbol}]</b>\n"
            f"{qty}주 @ {price:,.0f}원 | 손익 {pnl_krw:+,.0f}원")


def fmt_sl(symbol, entry, sl, pnl_krw) -> str:
    return (f"🔴 <b>SL 청산 [{symbol}]</b>\n"
            f"진입 {entry:,.0f} → 손절 {sl:,.0f}원 | 손익 {pnl_krw:+,.0f}원")


def fmt_counter(symbol, entry, price, reason) -> str:
    return (f"⚠️ <b>역신호 청산 [{symbol}]</b>\n"
            f"진입 {entry:,.0f} → 청산 {price:,.0f}원\n"
            f"사유: {reason}")


class Notifier:
    def __init__(self, cfg: Config, session=None):
        self.cfg = cfg
        self.session = session or requests

    def send(self, text: str) -> bool:
        try:
            self.session.post(
                f"https://api.telegram.org/bot{self.cfg.telegram_token}/sendMessage",
                json={"chat_id": self.cfg.telegram_chat, "text": text,
                      "parse_mode": "HTML"},
                timeout=10)
            return True
        except requests.RequestException as e:
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

    def alert_error(self, msg):
        return self.send(f"🛑 <b>에러</b>\n{msg}")
