"""api_server.py 테스트 — health / force-buy 인증·유효성·주문."""
import pytest
import api_server
from config import Config


def _cfg(**kw):
    base = dict(app_key="k", app_secret="s", cano="12345678",
                telegram_token="t", telegram_chat="c", admin_token="secret")
    base.update(kw)
    return Config(**base)


class MockBot:
    def __init__(self, cfg=None, order_id="ORD001"):
        self.cfg = cfg or _cfg()
        self.kill_switch = False
        self.entry_info = None
        self.daily_trade_count = 0
        self._order_id = order_id

        class _Client:
            def __init__(self, oid):
                self._oid = oid
            def place_buy_order(self, symbol, qty, price=None):
                return self._oid
        self.client = _Client(order_id)


@pytest.fixture(autouse=True)
def _reset_bot():
    """각 테스트 후 bot 참조 초기화."""
    yield
    api_server._bot_ref = None


@pytest.fixture()
def client():
    api_server.app.config["TESTING"] = True
    return api_server.app.test_client()


# ── /health ────────────────────────────────────────────
def test_health_starting(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.get_json()["status"] == "starting"


def test_health_ok(client):
    api_server.register_bot(MockBot())
    r = client.get("/health")
    d = r.get_json()
    assert d["status"] == "ok"
    assert d["kill_switch"] is False
    assert d["position"] is None


# ── /force-buy 인증 ─────────────────────────────────────
def test_force_buy_no_token(client):
    api_server.register_bot(MockBot())
    r = client.post("/force-buy", json={"symbol": "005930", "qty": 1})
    assert r.status_code == 401


def test_force_buy_wrong_token(client):
    api_server.register_bot(MockBot())
    r = client.post("/force-buy", json={"symbol": "005930", "qty": 1},
                    headers={"X-Auth-Token": "wrong"})
    assert r.status_code == 401


def test_force_buy_no_admin_token_set(client):
    """ADMIN_TOKEN 미설정이면 항상 401."""
    api_server.register_bot(MockBot(cfg=_cfg(admin_token="")))
    r = client.post("/force-buy", json={"symbol": "005930", "qty": 1},
                    headers={"X-Auth-Token": ""})
    assert r.status_code == 401


# ── /force-buy 유효성 ───────────────────────────────────
def test_force_buy_invalid_symbol(client):
    api_server.register_bot(MockBot())
    r = client.post("/force-buy", json={"symbol": "12345", "qty": 1},
                    headers={"X-Auth-Token": "secret"})
    assert r.status_code == 400


def test_force_buy_qty_over_limit(client):
    api_server.register_bot(MockBot())
    r = client.post("/force-buy", json={"symbol": "005930", "qty": 99},
                    headers={"X-Auth-Token": "secret"})
    assert r.status_code == 400


# ── /force-buy 성공 ─────────────────────────────────────
def test_force_buy_success(client):
    api_server.register_bot(MockBot(order_id="ORD999"))
    r = client.post("/force-buy", json={"symbol": "005930", "qty": 1},
                    headers={"X-Auth-Token": "secret"})
    d = r.get_json()
    assert r.status_code == 200
    assert d["ok"] is True
    assert d["order_id"] == "ORD999"
    assert d["symbol"] == "005930"


def test_force_buy_kis_failure(client):
    """KIS가 None 반환하면 500."""
    api_server.register_bot(MockBot(order_id=None))
    r = client.post("/force-buy", json={"symbol": "005930", "qty": 1},
                    headers={"X-Auth-Token": "secret"})
    assert r.status_code == 500
