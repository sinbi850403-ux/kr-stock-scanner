"""kis_client.py 테스트 — 응답 파싱·주문·모의/실전 TR·레이트·토큰 (mock)."""
import time
import pytest
from config import Config
import kis_client as kc


def _cfg(**kw):
    base = dict(app_key="k", app_secret="s", cano="12345678", acnt_prdt_cd="01",
                telegram_token="t", telegram_chat="c", rate_per_sec=10000.0)
    base.update(kw)
    return Config(**base)


# ── 캔들 파싱 ───────────────────────────────────────────
def test_get_candles_parses_ascending(monkeypatch):
    cli = kc.KISClient(_cfg())
    canned = {"output2": [
        {"stck_bsop_date": "20260609", "stck_oprc": "100", "stck_hgpr": "105",
         "stck_lwpr": "99", "stck_clpr": "104", "acml_vol": "1000"},
        {"stck_bsop_date": "20260608", "stck_oprc": "98", "stck_hgpr": "101",
         "stck_lwpr": "97", "stck_clpr": "100", "acml_vol": "900"},
    ]}
    monkeypatch.setattr(cli, "_get", lambda *a, **k: canned)
    df = cli.get_candles("005930", "D", 100)
    assert list(df["close"]) == [100.0, 104.0]          # 과거→현재 정렬
    assert {"open", "high", "low", "close", "volume"} <= set(df.columns)
    assert df["volume"].iloc[-1] == 1000


def test_get_candles_empty(monkeypatch):
    cli = kc.KISClient(_cfg())
    monkeypatch.setattr(cli, "_get", lambda *a, **k: {})
    df = cli.get_candles("005930", "D", 100)
    assert df.empty


def test_get_candles_passes_period(monkeypatch):
    cli = kc.KISClient(_cfg())
    captured = {}

    def fake_get(path, tr_id, params, **k):
        captured.update(params)
        captured["tr_id"] = tr_id
        return {}
    monkeypatch.setattr(cli, "_get", fake_get)
    cli.get_candles("005930", "W", 52)
    assert captured["FID_PERIOD_DIV_CODE"] == "W"
    assert captured["tr_id"] == "FHKST03010100"


# ── 현재가 파싱 ─────────────────────────────────────────
def test_get_current_price(monkeypatch):
    cli = kc.KISClient(_cfg())
    canned = {"output": {"stck_prpr": "70200", "stck_prdy_clpr": "69000",
                         "stck_uplmtprice": "89700", "stck_dnlmtprice": "48300",
                         "prdy_ctrt": "1.74"}}
    monkeypatch.setattr(cli, "_get", lambda *a, **k: canned)
    p = cli.get_current_price("005930")
    assert p["price"] == 70200
    assert p["prev_close"] == 69000
    assert p["upper"] == 89700
    assert p["lower"] == 48300


# ── 잔고 파싱 ───────────────────────────────────────────
def test_get_balance(monkeypatch):
    cli = kc.KISClient(_cfg())
    canned = {
        "output1": [
            {"pdno": "005930", "hldg_qty": "10", "pchs_avg_pric": "70000", "prpr": "71000"},
            {"pdno": "000660", "hldg_qty": "0", "pchs_avg_pric": "0", "prpr": "0"},
        ],
        "output2": [{"dnca_tot_amt": "5000000"}],
    }
    monkeypatch.setattr(cli, "_get", lambda *a, **k: canned)
    cash, holdings = cli.get_balance()
    assert cash == 5000000.0
    assert len(holdings) == 1                 # 수량 0 종목 제외
    assert holdings[0]["symbol"] == "005930"
    assert holdings[0]["qty"] == 10


def test_get_position_found(monkeypatch):
    cli = kc.KISClient(_cfg())
    monkeypatch.setattr(cli, "get_balance",
                        lambda: (1000.0, [{"symbol": "005930", "qty": 10, "avg": 70000, "price": 71000}]))
    pos = cli.get_position("005930")
    assert pos is not None and pos["qty"] == 10


def test_get_position_not_found(monkeypatch):
    cli = kc.KISClient(_cfg())
    monkeypatch.setattr(cli, "get_balance", lambda: (1000.0, []))
    assert cli.get_position("005930") is None


# ── 주문: 모의/실전 TR + 시장가/지정가 ──────────────────
def test_buy_market_paper(monkeypatch):
    cli = kc.KISClient(_cfg(is_paper=True))
    captured = {}

    def fake_post(path, tr_id, body):
        captured["path"] = path
        captured["tr_id"] = tr_id
        captured["body"] = body
        return {"output": {"KRX_FWDG_ORD_ID": "ORD123"}}
    monkeypatch.setattr(cli, "_post", fake_post)
    oid = cli.place_buy_order("005930", 10, price=None)  # 시장가
    assert oid == "ORD123"
    assert captured["tr_id"] == "VTTC0802U"               # 모의 매수
    assert captured["body"]["ORD_DVSN"] == kc.ORD_DVSN_MARKET
    assert captured["body"]["ORD_QTY"] == "10"


def test_buy_limit_real(monkeypatch):
    cli = kc.KISClient(_cfg(is_paper=False, account_pwd="0000"))
    captured = {}
    monkeypatch.setattr(cli, "_post", lambda path, tr_id, body: captured.update(
        {"tr_id": tr_id, "body": body}) or {"output": {"KRX_FWDG_ORD_ID": "X"}})
    cli.place_buy_order("005930", 5, price=70000)         # 지정가
    assert captured["tr_id"] == "TTTC0802U"               # 실전 매수
    assert captured["body"]["ORD_DVSN"] == kc.ORD_DVSN_LIMIT
    assert captured["body"]["ORD_UNPR"] == "70000"


def test_sell_uses_sell_tr(monkeypatch):
    cli = kc.KISClient(_cfg(is_paper=True))
    captured = {}
    monkeypatch.setattr(cli, "_post", lambda path, tr_id, body: captured.update(
        {"tr_id": tr_id}) or {"output": {"ODNO": "Y"}})
    oid = cli.place_sell_order("005930", 10, price=None)
    assert captured["tr_id"] == "VTTC0801U"               # 모의 매도
    assert oid == "Y"


def test_cancel_order(monkeypatch):
    cli = kc.KISClient(_cfg(is_paper=True))
    captured = {}
    monkeypatch.setattr(cli, "_post", lambda path, tr_id, body: captured.update(
        {"tr_id": tr_id, "path": path}) or {})
    cli.cancel_order("ORD123", "005930", 10)
    assert captured["tr_id"] == "VTTC0804U"
    assert "cancel" in captured["path"]


def test_get_pending_orders(monkeypatch):
    cli = kc.KISClient(_cfg())
    canned = {"output": [
        {"KRX_FWDG_ORD_ID": "O1", "pdno": "005930", "ord_qty": "10", "rmn_qty": "3"},
    ]}
    monkeypatch.setattr(cli, "_get", lambda *a, **k: canned)
    orders = cli.get_pending_orders()
    assert len(orders) == 1
    assert orders[0]["order_id"] == "O1"
    assert orders[0]["remain"] == 3


# ── 토큰 / _get (FakeSession) ───────────────────────────
class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._p = payload

    def json(self):
        return self._p

    def raise_for_status(self):
        if self.status_code >= 400:
            raise kc.requests.HTTPError(f"status {self.status_code}")


def test_token_cached():
    cli = kc.KISClient(_cfg())
    cli._token_value = "CACHED"
    cli._token_exp = time.time() + 1000
    assert cli._get_token() == "CACHED"


def test_token_fetch():
    cli = kc.KISClient(_cfg())

    class S:
        def post(self, url, json=None, timeout=None):
            return _Resp(200, {"access_token": "NEW", "expires_in": 86400})
    cli.session = S()
    assert cli._get_token() == "NEW"
    assert cli._token_value == "NEW"


def test_get_retries_then_succeeds(monkeypatch):
    cli = kc.KISClient(_cfg())
    cli._token_value = "T"
    cli._token_exp = time.time() + 1000
    monkeypatch.setattr(kc.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    class S:
        def get(self, url, headers=None, params=None, timeout=None):
            calls["n"] += 1
            if calls["n"] < 2:
                return _Resp(500, {})
            return _Resp(200, {"ok": True})
    cli.session = S()
    out = cli._get("/x", "TR", {})
    assert out == {"ok": True}
    assert calls["n"] == 2


def test_get_returns_empty_on_persistent_failure(monkeypatch):
    cli = kc.KISClient(_cfg())
    cli._token_value = "T"
    cli._token_exp = time.time() + 1000
    monkeypatch.setattr(kc.time, "sleep", lambda *_: None)

    class S:
        def get(self, url, headers=None, params=None, timeout=None):
            return _Resp(500, {})
    cli.session = S()
    assert cli._get("/x", "TR", {}, retries=1) == {}


# ── _post / _throttle / 순위 ────────────────────────────
def test_post_happy_path(monkeypatch):
    cli = kc.KISClient(_cfg())
    cli._token_value = "T"
    cli._token_exp = time.time() + 1000
    monkeypatch.setattr(cli, "_hashkey", lambda body: "H")

    class S:
        def post(self, url, headers=None, json=None, timeout=None):
            return _Resp(200, {"output": {"ODNO": "Z"}})
    cli.session = S()
    assert cli._post("/x", "TR", {"a": 1}) == {"output": {"ODNO": "Z"}}


def test_post_returns_empty_on_failure(monkeypatch):
    cli = kc.KISClient(_cfg())
    cli._token_value = "T"
    cli._token_exp = time.time() + 1000
    monkeypatch.setattr(cli, "_hashkey", lambda body: "H")

    class S:
        def post(self, url, headers=None, json=None, timeout=None):
            r = _Resp(403, {})
            r.text = "denied"
            return r
    cli.session = S()
    assert cli._post("/x", "TR", {}) == {}


def test_throttle_runs():
    cli = kc.KISClient(_cfg(rate_per_sec=1000.0))
    cli._throttle()
    cli._throttle()   # 예외 없이 동작


def test_volume_rank_parses(monkeypatch):
    cli = kc.KISClient(_cfg())
    monkeypatch.setattr(cli, "_get", lambda *a, **k: {
        "output": [{"mksc_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자"}]})
    assert cli.volume_rank("1") == [("005930", "삼성전자")]


def test_fluctuation_rank_parses(monkeypatch):
    cli = kc.KISClient(_cfg())
    monkeypatch.setattr(cli, "_get", lambda *a, **k: {
        "output": [{"stck_shrn_iscd": "000660", "hts_kor_isnm": "SK하이닉스"}]})
    assert cli.fluctuation_rank() == [("000660", "SK하이닉스")]
