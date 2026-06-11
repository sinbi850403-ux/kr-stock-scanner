"""
메인 상태머신 — 비반복 2단계 자동매매 루프.

상태:
  - 포지션 보유 → 감시(TP/SL/역신호)
  - 미보유 + 진입창(익일 09:10~09:40) + pending → 진입
  - 미보유 + 마감후(15:40~) → 확정 스캔 → pending 저장 + 알림
  - 미보유 + 장중(09:10~15:20) → 잠정 알림만 (주문 X)

안전: 모의투자 기본 / 킬스위치(일손실 한도) / 일일 거래·포지션 한도 /
      재시작 시 KIS 잔고로 복구.
"""
import os
import json
import time
import logging
from dataclasses import asdict
from datetime import datetime

import pytz

import gates
import strategy
import api_server
from config import Config
from kis_client import KISClient
from scanner import Scanner
from trader import AutoTrader, EntryInfo
from notify import Notifier
from strategy import Signal

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("main")
KST = pytz.timezone("Asia/Seoul")


class TradingBot:
    def __init__(self, cfg: Config, client, scanner, trader, notifier,
                 state_path="state_recovery.json", holidays=None):
        self.cfg = cfg
        self.client = client
        self.scanner = scanner
        self.trader = trader
        self.notifier = notifier
        self.state_path = state_path
        self.holidays = holidays or set()

        self.entry_info = None
        self.pending_signals = []        # [(name, Signal)]
        self.daily_trade_count = 0
        self.daily_loss_krw = 0.0
        self.daily_log = []              # [{symbol, time}]
        self.alerted = set()             # {f"{code}_{yyyymmdd}"}
        self.kill_switch = False
        self.start_balance = 0.0
        self.last_reset_date = None
        self.counter_checked = None

    # ── 상태 영속화 ──
    def save_state(self):
        state = {
            "entry_info": asdict(self.entry_info) if self.entry_info else None,
            "pending_signals": [(n, asdict(s)) for n, s in self.pending_signals],
            "daily_trade_count": self.daily_trade_count,
            "daily_loss_krw": self.daily_loss_krw,
            "kill_switch": self.kill_switch,
            "start_balance": self.start_balance,
            "last_reset_date": self.last_reset_date,
            "alerted": list(self.alerted),
        }
        try:
            with open(self.state_path, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except OSError as e:
            log.error("상태 저장 실패: %s", e)

    def load_state(self):
        if not os.path.exists(self.state_path):
            return
        try:
            with open(self.state_path, encoding="utf-8") as f:
                s = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            log.error("상태 로드 실패: %s", e)
            return
        self.entry_info = EntryInfo(**s["entry_info"]) if s.get("entry_info") else None
        self.pending_signals = [(n, Signal(**d)) for n, d in s.get("pending_signals", [])]
        self.daily_trade_count = s.get("daily_trade_count", 0)
        self.daily_loss_krw = s.get("daily_loss_krw", 0.0)
        self.kill_switch = s.get("kill_switch", False)
        self.start_balance = s.get("start_balance", 0.0)
        self.last_reset_date = s.get("last_reset_date")
        self.alerted = set(s.get("alerted", []))

    # ── 재시작 복구 ──
    def startup_recovery(self):
        self.load_state()
        try:
            _, holdings = self.client.get_balance()
        except Exception as e:                       # noqa: BLE001
            log.error("복구 중 잔고조회 실패: %s", e)
            return
        held = {h["symbol"]: h for h in holdings}
        if self.entry_info:
            pos = held.get(self.entry_info.symbol)
            if not pos:
                log.info("복구: 저장된 포지션이 KIS에 없음 → 청산 처리")
                self.entry_info = None
            else:
                self.entry_info.holding_qty = pos["qty"]   # 실제 잔고로 동기화
                log.info("복구: 포지션 동기화 %s qty=%s", self.entry_info.symbol, pos["qty"])
        elif holdings:
            # 수동 매수 포지션은 텔레그램 스팸 없이 로그만 남김
            log.warning("미등록 보유 포지션(수동): %s — 봇이 관리하지 않음", list(held))
        self.save_state()

    # ── 일일 리셋 ──
    def _daily_reset_if_needed(self, now):
        ds = now.strftime("%Y%m%d")
        if self.last_reset_date == ds:
            return
        self.last_reset_date = ds
        self.daily_trade_count = 0
        self.daily_loss_krw = 0.0
        self.pending_signals = []
        self.daily_log = []
        self.alerted = set()
        self.counter_checked = None
        self.kill_switch = False
        try:
            self.start_balance = self.client.get_balance()[0]
        except Exception:                            # noqa: BLE001
            self.start_balance = 0.0

    # ── 페이즈 ──
    def _phase(self, now):
        if now.weekday() >= 5 or now.strftime("%Y%m%d") in self.holidays:
            return "closed"
        hhmm = now.strftime("%H%M")
        if hhmm >= self.cfg.final_scan_hhmm:
            return "postclose"
        if self.cfg.entry_start <= hhmm < self.cfg.entry_open_end:
            return "entry"
        if self.cfg.scan_start <= hhmm < self.cfg.entry_end:
            return "scan"
        return "idle"

    # ── 메인 사이클 ──
    def run_cycle(self, now=None):
        now = now or datetime.now(KST)
        self._daily_reset_if_needed(now)
        if self.kill_switch:
            return
        phase = self._phase(now)
        if phase == "closed":
            return

        if self.entry_info is not None:
            self._manage_position(now)
            return

        if phase == "entry":
            if self.pending_signals:
                self._try_enter(now)
            else:
                self._intraday_alert_scan(now)   # pending 없으면 계속 스캔
        elif phase == "postclose":
            self._post_close_scan(now)
        elif phase == "scan":
            self._intraday_alert_scan(now)
        self.save_state()

    # ── 포지션 감시 ──
    def _check_counter(self, symbol, now):
        ds = now.strftime("%Y%m%d")
        if self.counter_checked == ds:
            return False, ""
        self.counter_checked = ds
        df_d = self.client.get_candles(symbol, "D", self.cfg.daily_count)
        df_w = self.client.get_candles(symbol, "W", self.cfg.weekly_count)
        df_m = self.client.get_candles(symbol, "M", self.cfg.monthly_count)
        return strategy.counter_exit(df_d, df_w, df_m, self.cfg)

    def _manage_position(self, now):
        ei = self.entry_info
        pinfo = self.client.get_current_price(ei.symbol)
        cur = pinfo["price"]
        counter, reason = self._check_counter(ei.symbol, now)
        result = self.trader.monitor(ei, cur, counter=counter, counter_reason=reason)
        if result is None:
            pnl = (cur - ei.entry_price) * ei.holding_qty
            self._on_close(pnl, now)
            self.entry_info = None
        else:
            self.entry_info = result
        self.save_state()

    # ── 진입 ──
    def _try_enter(self, now):
        if not self.pending_signals:
            return
        name, sig = self.pending_signals[0]
        pinfo = self.client.get_current_price(sig.symbol)
        cur, prev = pinfo["price"], pinfo["prev_close"]

        if sig.entry_price and abs(cur - sig.entry_price) / sig.entry_price > 0.05:
            log.info("신호 만료 %s (현재가 괴리)", sig.symbol)
            self.pending_signals.pop(0)
            return

        ok, reason = gates.validate_signal(
            open_price=cur, prev_close=prev, current_price=cur, now=now,
            daily_trade_count=self.daily_trade_count, current_positions=0,
            symbol=sig.symbol, daily_log=self.daily_log, cfg=self.cfg,
            holidays=self.holidays)
        if not ok:
            log.info("게이트 거부 %s: %s", sig.symbol, reason)
            self.pending_signals.pop(0)
            return

        ei = self.trader.execute_signal(sig, name, now)
        if ei:
            self.entry_info = ei
            self.daily_trade_count += 1
            self.daily_log.append({"symbol": sig.symbol, "time": now})
            self.pending_signals = []
        self.save_state()

    # ── 스캔 ──
    def _post_close_scan(self, now):
        skip = {self.entry_info.symbol} if self.entry_info else set()
        sigs = self.scanner.scan(skip_symbols=skip, now=now)
        self.pending_signals = sigs
        for name, sig in sigs:
            key = f"{sig.symbol}_{now:%Y%m%d}"
            if key not in self.alerted:
                self.notifier.alert_scan_signal(sig, name, provisional=False)
                self.alerted.add(key)

    def _intraday_alert_scan(self, now):
        for name, sig in self.scanner.scan(now=now):
            key = f"{sig.symbol}_{now:%Y%m%d}"
            if key not in self.alerted:
                self.notifier.alert_scan_signal(sig, name, provisional=True)
                self.alerted.add(key)

    # ── 청산 후처리 ──
    def _on_close(self, pnl, now):
        self.daily_trade_count += 1
        if pnl < 0:
            self.daily_loss_krw += -pnl
        limit = (self.start_balance or 0) * self.cfg.daily_max_loss_pct
        if limit > 0 and self.daily_loss_krw >= limit:
            self.kill_switch = True
            log.warning("🛑 킬스위치 발동 — 일손실 %.0f >= 한도 %.0f",
                        self.daily_loss_krw, limit)
            self.notifier.alert_error(
                f"🛑 킬스위치 발동 — 금일 손실 {self.daily_loss_krw:,.0f}원, 거래 중단")


def build_bot(cfg: Config) -> TradingBot:
    client = KISClient(cfg)
    return TradingBot(cfg, client, Scanner(cfg, client),
                      AutoTrader(cfg, client, Notifier(cfg)), Notifier(cfg))


def main():
    cfg = Config.from_env()
    cfg.validate()
    bot = build_bot(cfg)
    mode = "모의투자" if cfg.is_paper else "⚠️실전"
    bot.notifier.send(f"🤖 국내주식 컨플루언스 자동매매 ON [{mode}]\n"
                      f"기준 {cfg.threshold}/7 | 리스크 {cfg.risk_pct*100:.0f}% | "
                      f"포지션 최대 {cfg.max_positions}")
    api_server.register_bot(bot)
    api_server.start()
    bot.startup_recovery()
    while True:
        try:
            bot.run_cycle()
        except Exception as e:                        # noqa: BLE001
            log.exception("루프 오류: %s", e)
        time.sleep(cfg.scan_interval)


if __name__ == "__main__":
    main()
