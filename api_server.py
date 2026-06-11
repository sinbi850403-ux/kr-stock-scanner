"""
관리 HTTP API — Flask 백그라운드 스레드로 실행.

엔드포인트:
  GET  /health      — 봇 상태 확인
  POST /force-buy   — 강제 1주 매수 (테스트/긴급 진입용)
    헤더: X-Auth-Token: <BOT_CMD_CODE>
    바디: {"symbol": "005930", "qty": 1}
"""
import logging
import os
import threading

from flask import Flask, jsonify, request

log = logging.getLogger(__name__)
app = Flask(__name__)

_bot_ref = None   # TradingBot 인스턴스 주입


def register_bot(bot):
    global _bot_ref
    _bot_ref = bot


def _check_token():
    token = request.headers.get("X-Auth-Token", "")
    admin = (_bot_ref.cfg.admin_token if _bot_ref else "") or ""
    if not admin:
        return False, "BOT_CMD_CODE 미설정"
    if token != admin:
        return False, "토큰 불일치"
    return True, ""


@app.route("/health")
def health():
    if _bot_ref is None:
        return jsonify({"status": "starting"}), 200
    return jsonify({
        "status": "ok",
        "kill_switch": _bot_ref.kill_switch,
        "position": _bot_ref.entry_info.symbol if _bot_ref.entry_info else None,
        "daily_trades": _bot_ref.daily_trade_count,
    })


@app.route("/force-buy", methods=["POST"])
def force_buy():
    ok, reason = _check_token()
    if not ok:
        return jsonify({"error": reason}), 401

    data = request.get_json(silent=True) or {}
    symbol = data.get("symbol", "").strip()
    qty = int(data.get("qty", 1))

    if not symbol or len(symbol) != 6 or not symbol.isdigit():
        return jsonify({"error": "symbol 6자리 숫자 필요 (예: 005930)"}), 400
    if qty < 1 or qty > 10:
        return jsonify({"error": "qty 1~10만 허용"}), 400

    if _bot_ref is None:
        return jsonify({"error": "봇 초기화 중"}), 503

    try:
        order_id = _bot_ref.client.place_buy_order(symbol, qty, price=None)  # 시장가
        if order_id:
            log.info("force-buy 성공 %s %d주 → 주문번호 %s", symbol, qty, order_id)
            return jsonify({"ok": True, "order_id": order_id, "symbol": symbol, "qty": qty})
        return jsonify({"error": "주문 실패 (KIS 응답 없음)"}), 500
    except Exception as e:   # noqa: BLE001
        log.error("force-buy 오류: %s", e)
        return jsonify({"error": str(e)}), 500


def start(port: int | None = None):
    port = port or int(os.environ.get("PORT", 8080))
    t = threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=port, use_reloader=False),
        daemon=True,
        name="api-server",
    )
    t.start()
    log.info("관리 API 서버 시작: port=%d", port)
