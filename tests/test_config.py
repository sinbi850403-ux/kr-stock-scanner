"""config.py 테스트 — 설정·모의/실전 전환·검증."""
import pytest
from config import Config


# ── 기본값 ──────────────────────────────────────────────
def test_default_is_paper_true():
    """플래그 없으면 모의투자가 기본."""
    c = Config()
    assert c.is_paper is True


def test_default_indicator_params():
    c = Config()
    assert (c.ema_fast, c.ema_mid, c.ema_slow) == (5, 20, 60)
    assert c.rsi_period == 14
    assert c.rsi_buy == 45.0
    assert (c.macd_fast, c.macd_slow, c.macd_signal) == (12, 26, 9)
    assert c.atr_period == 14


def test_default_risk_and_safety():
    c = Config()
    assert c.risk_pct == 0.01
    assert c.max_positions == 1
    assert c.max_daily_trades == 3
    assert c.daily_max_loss_pct == 0.03
    assert (c.tp1_r, c.tp2_r, c.tp3_r) == (0.8, 1.5, 2.5)


# ── base_url 전환 ───────────────────────────────────────
def test_base_url_paper():
    c = Config(is_paper=True)
    assert c.base_url == "https://openapivts.koreainvestment.com:29443"


def test_base_url_real():
    c = Config(is_paper=False)
    assert c.base_url == "https://openapi.koreainvestment.com:9443"


# ── TR_ID 전환 (모의=V, 실전=T) ─────────────────────────
def test_tr_id_buy_paper():
    assert Config(is_paper=True).tr_id("buy") == "VTTC0802U"


def test_tr_id_buy_real():
    assert Config(is_paper=False).tr_id("buy") == "TTTC0802U"


def test_tr_id_sell_paper_real():
    assert Config(is_paper=True).tr_id("sell") == "VTTC0801U"
    assert Config(is_paper=False).tr_id("sell") == "TTTC0801U"


def test_tr_id_balance():
    assert Config(is_paper=True).tr_id("balance") == "VTTC8434R"
    assert Config(is_paper=False).tr_id("balance") == "TTTC8434R"


def test_tr_id_quotation_same_for_both():
    """시세조회 TR은 모의/실전 동일."""
    assert Config(is_paper=True).tr_id("daily") == "FHKST03010100"
    assert Config(is_paper=False).tr_id("daily") == "FHKST03010100"
    assert Config(is_paper=True).tr_id("price") == "FHKST01010100"


def test_tr_id_unknown_raises():
    with pytest.raises(KeyError):
        Config().tr_id("does_not_exist")


# ── from_env: 모의/실전 3중 플래그 ──────────────────────
def _set_required_env(monkeypatch):
    monkeypatch.setenv("KIS_APP_KEY", "key")
    monkeypatch.setenv("KIS_APP_SECRET", "secret")
    monkeypatch.setenv("TELEGRAM_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    monkeypatch.setenv("KIS_ACCOUNT", "12345678-01")


def test_from_env_defaults_to_paper(monkeypatch):
    _set_required_env(monkeypatch)
    monkeypatch.delenv("ENABLE_REAL_TRADING", raising=False)
    monkeypatch.delenv("REAL_TRADING_CONFIRM", raising=False)
    c = Config.from_env()
    assert c.is_paper is True
    assert c.app_key == "key"
    assert c.cano == "12345678"
    assert c.acnt_prdt_cd == "01"


def test_from_env_no_flag_stays_paper(monkeypatch):
    _set_required_env(monkeypatch)
    monkeypatch.delenv("ENABLE_REAL_TRADING", raising=False)
    assert Config.from_env().is_paper is True


def test_from_env_flag_enables_real(monkeypatch):
    _set_required_env(monkeypatch)
    monkeypatch.setenv("ENABLE_REAL_TRADING", "1")
    assert Config.from_env().is_paper is False


def test_from_env_flag_yes_enables_real(monkeypatch):
    _set_required_env(monkeypatch)
    monkeypatch.setenv("ENABLE_REAL_TRADING", "YES")
    assert Config.from_env().is_paper is False


def test_from_env_overrides(monkeypatch):
    _set_required_env(monkeypatch)
    monkeypatch.setenv("THRESHOLD", "6")
    monkeypatch.setenv("RATE_PER_SEC", "4")
    c = Config.from_env()
    assert c.threshold == 6
    assert c.rate_per_sec == 4.0


# ── validate ────────────────────────────────────────────
def test_validate_ok():
    c = Config(app_key="k", app_secret="s", cano="12345678",
               telegram_token="t", telegram_chat="c")
    c.validate()  # 예외 없음


def test_validate_missing_keys_raises():
    with pytest.raises(ValueError, match="KIS_APP_KEY"):
        Config().validate()


def test_validate_real_requires_account_pwd():
    c = Config(app_key="k", app_secret="s", cano="12345678",
               telegram_token="t", telegram_chat="c",
               is_paper=False, account_pwd="")
    with pytest.raises(ValueError, match="비밀번호"):
        c.validate()


def test_validate_high_risk_warns(caplog):
    c = Config(app_key="k", app_secret="s", cano="12345678",
               telegram_token="t", telegram_chat="c", risk_pct=0.30)
    c.validate()
    assert any("리스크" in r.message for r in caplog.records)


def test_default_scan_start():
    """장전 스캔 시작 시각은 기본 0830."""
    assert Config().scan_start == "0830"


def test_tr_id_cap_rank_same_for_both():
    """시가총액 상위 랭킹 TR은 모의/실전 동일."""
    assert Config(is_paper=True).tr_id("cap_rank") == "FHPST01740000"
    assert Config(is_paper=False).tr_id("cap_rank") == "FHPST01740000"


def test_default_max_ext_pct():
    """과열 필터 기본값 — EMA20 대비 +12% 초과 시 진입 거부."""
    assert Config().max_ext_pct == 12.0
