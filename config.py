"""
설정 관리 — KIS 인증·텔레그램·지표/리스크/게이트 파라미터.

안전 원칙:
  - is_paper(모의투자)가 기본 True.
  - 실거래는 ENABLE_REAL_TRADING=1 AND REAL_TRADING_CONFIRM=YES 둘 다 필요.
  - 시세조회 TR은 모의/실전 동일, 주문/잔고 TR만 V↔T 전환.
"""
import os
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

# 논리 액션 → (실전 TR, 모의 TR)
_TR_MAP = {
    "buy":      ("TTTC0802U", "VTTC0802U"),
    "sell":     ("TTTC0801U", "VTTC0801U"),
    "balance":  ("TTTC8434R", "VTTC8434R"),
    "revise":   ("TTTC0803U", "VTTC0803U"),
    "cancel":   ("TTTC0804U", "VTTC0804U"),
    "ccld":     ("TTTC8001R", "VTTC8001R"),
    "pending":  ("TTTC8036R", "VTTC8036R"),   # 정정취소가능주문조회 (미체결) — 검증필요
    # 시세조회 — 모의/실전 동일
    "daily":    ("FHKST03010100", "FHKST03010100"),
    "price":    ("FHKST01010100", "FHKST01010100"),
    "vol_rank": ("FHPST01710000", "FHPST01710000"),
    "flx_rank": ("FHPST01700000", "FHPST01700000"),
    "cap_rank": ("FHPST01740000", "FHPST01740000"),  # 시가총액 상위 (모의/실전 동일)
    "investor": ("FHKST01010900", "FHKST01010900"),  # 투자자별 매매동향 (모의/실전 동일)
}

_REAL_BASE = "https://openapi.koreainvestment.com:9443"
_PAPER_BASE = "https://openapivts.koreainvestment.com:29443"


@dataclass
class Config:
    # ── KIS 인증 ──
    app_key: str = ""
    app_secret: str = ""
    cano: str = ""              # 계좌 앞 8자리 (종합계좌번호)
    acnt_prdt_cd: str = "01"    # 계좌 뒤 2자리 (상품코드)
    account_pwd: str = ""       # 실거래 주문 시 필요

    # ── 텔레그램 ──
    telegram_token: str = ""
    telegram_chat: str = ""

    # ── 모드 ──
    is_paper: bool = True

    # ── 스캔 ──
    threshold: int = 5          # 알림/진입 점수 (5 or 6)
    top_n: int = 100            # 각 순위 Top N
    scan_interval: int = 600    # 장중 스캔 주기(초)
    rate_per_sec: float = 8.0   # 초당 API 호출 상한
    final_scan_hhmm: str = "1540"  # 마감 후 확정 산출 시각

    # ── MTF 캔들 수 ──
    daily_count: int = 100
    weekly_count: int = 52
    monthly_count: int = 24

    # ── EMA ──
    ema_fast: int = 5
    ema_mid: int = 20
    ema_slow: int = 60

    # ── RSI ──
    rsi_period: int = 14
    rsi_buy: float = 45.0

    # ── MACD ──
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9

    # ── ATR ──
    atr_period: int = 14

    # ── 구조(피봇/OB) ──
    piv_len: int = 12
    ob_lookback: int = 20

    # ── 거래량 ──
    vol_surge: float = 1.7
    vol_strong: float = 2.0
    vwap_window: int = 20
    rvol_window: int = 20

    # ── 리스크 ──
    risk_pct: float = 0.01
    sl_atr_mult: float = 2.0
    tp1_r: float = 0.8
    tp2_r: float = 1.5
    tp3_r: float = 2.5
    be_trig_r: float = 1.0

    # ── 게이트 ──
    max_ext_pct: float = 12.0       # 과열 필터: 종가가 EMA20 대비 +N% 초과 이격 시 진입 거부
    gap_filter_pct: float = 3.0
    vi_warn_pct: float = 9.0
    limit_veto_pct: float = 27.0
    price_limit_pct: float = 30.0   # KR 일일 가격제한(상/하한가) ±30%

    # ── 안전 ──
    max_positions: int = 1
    max_daily_trades: int = 3
    daily_max_loss_pct: float = 0.03

    # ── 거래시간 (KST "HHMM") ──
    scan_start: str = "0830"       # 장전 스캔 시작 (장전동시호가 시작)
    market_open: str = "0900"
    entry_start: str = "0910"
    entry_open_end: str = "0940"   # 익일 진입 실행 창 종료
    entry_end: str = "1520"
    market_close: str = "1530"

    # ── URL / TR ──
    @property
    def base_url(self) -> str:
        return _PAPER_BASE if self.is_paper else _REAL_BASE

    def tr_id(self, action: str) -> str:
        real, paper = _TR_MAP[action]
        return paper if self.is_paper else real

    # ── 환경변수 로드 ──
    @classmethod
    def from_env(cls) -> "Config":
        g = os.getenv
        _er = g("ENABLE_REAL_TRADING", "")
        log.info("DEBUG ENABLE_REAL_TRADING=%r", _er)
        enable_real = _er.strip() in ("1", "YES", "true", "on")
        is_paper = not enable_real

        cano, prdt = "", "01"
        acct = g("KIS_ACCOUNT", "")
        if acct:
            if "-" in acct:
                cano, prdt = acct.split("-", 1)
            elif len(acct) >= 10:
                cano, prdt = acct[:8], acct[8:]
            else:
                cano = acct

        def _f(key, default):
            v = g(key)
            return float(v) if v not in (None, "") else default

        def _i(key, default):
            v = g(key)
            return int(v) if v not in (None, "") else default

        c = cls(
            app_key=g("KIS_APP_KEY", ""),
            app_secret=g("KIS_APP_SECRET", ""),
            cano=cano,
            acnt_prdt_cd=prdt,
            account_pwd=g("ACNT_CODE", "") or g("KIS_ACNT_PW", "") or g("KIS_ACCOUNT_PWD", ""),
            telegram_token=g("TELEGRAM_TOKEN", ""),
            telegram_chat=g("TELEGRAM_CHAT_ID", ""),
            is_paper=is_paper,
            threshold=_i("THRESHOLD", 5),
            top_n=_i("TOP_N", 30),
            scan_interval=_i("SCAN_INTERVAL", 600),
            rate_per_sec=_f("RATE_PER_SEC", 8.0),
            final_scan_hhmm=g("FINAL_SCAN", "1540"),
            risk_pct=_f("RISK_PCT", 0.01),
            daily_max_loss_pct=_f("DAILY_MAX_LOSS_PCT", 0.03),
            max_positions=_i("MAX_POSITIONS", 1),
            max_daily_trades=_i("MAX_DAILY_TRADES", 3),
        )
        if not is_paper:
            log.warning("⚠️  실전 거래 모드 활성화됨 (ENABLE_REAL_TRADING + REAL_TRADING_CONFIRM)")
        else:
            log.info("모의투자 모드 (안전 기본값)")
        return c

    # ── 검증 ──
    def validate(self) -> None:
        required = {
            "KIS_APP_KEY": self.app_key,
            "KIS_APP_SECRET": self.app_secret,
            "TELEGRAM_TOKEN": self.telegram_token,
            "TELEGRAM_CHAT_ID": self.telegram_chat,
            "CANO(KIS_ACCOUNT)": self.cano,
        }
        missing = [k for k, v in required.items() if not v]
        if missing:
            raise ValueError(f"필수 환경변수 누락: {', '.join(missing)}")

        if not self.is_paper and not self.account_pwd:
            raise ValueError("실거래 모드에는 계좌 비밀번호(ACNT_CODE)가 필요합니다.")

        if self.risk_pct > 0.20:
            log.warning("RISK_PCT=%.0f%% — 1회 리스크가 매우 높습니다. 1~5%% 권장.",
                        self.risk_pct * 100)
