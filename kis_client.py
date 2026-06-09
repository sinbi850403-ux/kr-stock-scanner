"""
KIS REST API 래퍼 — 캔들/현재가/순위/주문/잔고/미체결.

안전: TR_ID는 cfg.tr_id()로 모의(V)/실전(T) 자동 전환.
주문가능 시각구분(ORD_DVSN) 등 일부 값은 모의투자로 검증 필요 (docs/DESIGN.md §10).
"""
import time
import logging
import threading
from datetime import datetime, timedelta

import requests
import pandas as pd
import pytz

log = logging.getLogger(__name__)
KST = pytz.timezone("Asia/Seoul")

# 주문구분 (검증필요): 통상 00=지정가, 01=시장가
ORD_DVSN_LIMIT = "00"
ORD_DVSN_MARKET = "01"

_CANDLE_REN = {
    "stck_bsop_date": "date", "stck_clpr": "close", "stck_oprc": "open",
    "stck_hgpr": "high", "stck_lwpr": "low", "acml_vol": "volume",
}


def _num(d: dict, *keys, default=0.0):
    for k in keys:
        v = d.get(k)
        if v not in (None, "", "0") or (v == "0"):
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return default


class KISClient:
    def __init__(self, cfg, session=None):
        self.cfg = cfg
        self.session = session or requests.Session()
        self._token_value = None
        self._token_exp = 0.0
        self._rate_lock = threading.Lock()
        self._last_call = 0.0

    # ── 레이트리밋 ──
    def _throttle(self):
        min_gap = 1.0 / self.cfg.rate_per_sec
        with self._rate_lock:
            now = time.monotonic()
            wait = self._last_call + min_gap - now
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()

    # ── 토큰 ──
    def _get_token(self):
        if self._token_value and time.time() < self._token_exp:
            return self._token_value
        r = self.session.post(f"{self.cfg.base_url}/oauth2/tokenP", json={
            "grant_type": "client_credentials",
            "appkey": self.cfg.app_key,
            "appsecret": self.cfg.app_secret,
        }, timeout=10)
        r.raise_for_status()
        d = r.json()
        self._token_value = d["access_token"]
        self._token_exp = time.time() + int(d.get("expires_in", 86400)) - 120
        return self._token_value

    def _headers(self, tr_id, extra=None):
        h = {
            "authorization": f"Bearer {self._get_token()}",
            "appkey": self.cfg.app_key,
            "appsecret": self.cfg.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
            "content-type": "application/json; charset=utf-8",
        }
        if extra:
            h.update(extra)
        return h

    def _hashkey(self, body: dict) -> str:
        r = self.session.post(f"{self.cfg.base_url}/uapi/hashkey", json=body, headers={
            "appkey": self.cfg.app_key, "appsecret": self.cfg.app_secret,
            "content-type": "application/json; charset=utf-8",
        }, timeout=10)
        r.raise_for_status()
        return r.json().get("HASH", "")

    # ── GET (조회) ──
    def _get(self, path, tr_id, params, retries=2):
        headers = self._headers(tr_id)
        for attempt in range(retries + 1):
            self._throttle()
            try:
                r = self.session.get(f"{self.cfg.base_url}{path}",
                                     headers=headers, params=params, timeout=10)
                if r.status_code == 200:
                    return r.json()
                log.warning("GET %s 실패 status=%s (재시도 %d)", path, r.status_code, attempt)
                time.sleep(0.5 * (attempt + 1))
            except requests.RequestException as e:
                log.warning("GET %s 예외 %s (재시도 %d)", path, e, attempt)
                time.sleep(0.5 * (attempt + 1))
        return {}

    # ── POST (주문) ──
    def _post(self, path, tr_id, body):
        headers = self._headers(tr_id, {"hashkey": self._hashkey(body)})
        self._throttle()
        try:
            r = self.session.post(f"{self.cfg.base_url}{path}",
                                  headers=headers, json=body, timeout=10)
            if r.status_code == 200:
                return r.json()
            log.error("POST %s 실패 status=%s body=%s", path, r.status_code, r.text)
        except requests.RequestException as e:
            log.error("POST %s 예외 %s", path, e)
        return {}

    # ── 캔들 ──
    def get_candles(self, symbol, period="D", count=100) -> pd.DataFrame:
        end = datetime.now(KST).strftime("%Y%m%d")
        span = {"D": count * 2 + 30, "W": count * 8 + 60, "M": count * 32 + 90}.get(period, count * 2 + 30)
        start = (datetime.now(KST) - timedelta(days=span)).strftime("%Y%m%d")
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": symbol,
            "FID_INPUT_DATE_1": start,
            "FID_INPUT_DATE_2": end,
            "FID_PERIOD_DIV_CODE": period,
            "FID_ORG_ADJ_PRC": "0",
        }
        j = self._get("/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
                      self.cfg.tr_id("daily"), params)
        rows = j.get("output2") or []
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows).rename(columns={k: v for k, v in _CANDLE_REN.items()})
        for c in ["open", "high", "low", "close", "volume"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna(subset=["close"])
        df = df[df["close"] > 0].iloc[::-1].reset_index(drop=True)   # 과거→현재
        return df

    # ── 현재가 ──
    def get_current_price(self, symbol) -> dict:
        j = self._get("/uapi/domestic-stock/v1/quotations/inquire-price",
                      self.cfg.tr_id("price"),
                      {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol})
        o = j.get("output") or {}
        return {
            "price": _num(o, "stck_prpr"),
            "prev_close": _num(o, "stck_prdy_clpr"),
            "upper": _num(o, "stck_uplmtprice"),
            "lower": _num(o, "stck_dnlmtprice"),
            "change_pct": _num(o, "prdy_ctrt"),
        }

    # ── 잔고 ──
    def get_balance(self):
        params = {
            "CANO": self.cfg.cano, "ACNT_PRDT_CD": self.cfg.acnt_prdt_cd,
            "AFHR_FLPR_YN": "N", "OFL_YN": "N", "INQR_DVSN": "01",
            "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "00",
            "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
        }
        j = self._get("/uapi/domestic-stock/v1/trading/inquire-balance",
                      self.cfg.tr_id("balance"), params)
        holdings, cash = [], 0.0
        for key in ("output1", "output2"):
            block = j.get(key) or []
            if isinstance(block, dict):
                block = [block]
            for r in block:
                if r.get("pdno"):
                    qty = int(_num(r, "hldg_qty"))
                    if qty > 0:
                        holdings.append({
                            "symbol": r["pdno"], "qty": qty,
                            "avg": _num(r, "pchs_avg_pric"),
                            "price": _num(r, "prpr"),
                        })
                else:
                    v = _num(r, "dnca_tot_amt", "prvs_rcdl_excc_amt", "nass_amt")
                    if v:
                        cash = v
        return cash, holdings

    def get_position(self, symbol):
        _, holdings = self.get_balance()
        for h in holdings:
            if h["symbol"] == symbol:
                return h
        return None

    # ── 주문 ──
    def _order_body(self, symbol, qty, price):
        dvsn = ORD_DVSN_MARKET if price is None else ORD_DVSN_LIMIT
        return {
            "CANO": self.cfg.cano, "ACNT_PRDT_CD": self.cfg.acnt_prdt_cd,
            "PDNO": symbol, "ORD_DVSN": dvsn,
            "ORD_QTY": str(int(qty)), "ORD_UNPR": str(int(price or 0)),
        }

    def place_buy_order(self, symbol, qty, price=None):
        j = self._post("/uapi/domestic-stock/v1/trading/order-cash",
                       self.cfg.tr_id("buy"), self._order_body(symbol, qty, price))
        out = j.get("output") or {}
        return out.get("KRX_FWDG_ORD_ID") or out.get("ODNO")

    def place_sell_order(self, symbol, qty, price=None):
        j = self._post("/uapi/domestic-stock/v1/trading/order-cash",
                       self.cfg.tr_id("sell"), self._order_body(symbol, qty, price))
        out = j.get("output") or {}
        return out.get("KRX_FWDG_ORD_ID") or out.get("ODNO")

    def cancel_order(self, order_id, symbol, qty, price=0):
        body = {
            "CANO": self.cfg.cano, "ACNT_PRDT_CD": self.cfg.acnt_prdt_cd,
            "KRX_FWDG_ORD_ID": order_id, "ORGN_ODNO": order_id,
            "PDNO": symbol, "ORD_DVSN": ORD_DVSN_LIMIT,
            "RVSE_CNCL_DVSN_CD": "02",   # 02 = 취소
            "ORD_QTY": str(int(qty)), "ORD_UNPR": str(int(price)),
            "QTY_ALL_ORD_YN": "Y",
        }
        return self._post("/uapi/domestic-stock/v1/trading/order-cancel",
                          self.cfg.tr_id("cancel"), body)

    def get_pending_orders(self):
        params = {
            "CANO": self.cfg.cano, "ACNT_PRDT_CD": self.cfg.acnt_prdt_cd,
            "INQR_DVSN_1": "0", "INQR_DVSN_2": "0",
            "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
        }
        j = self._get("/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl",
                      self.cfg.tr_id("pending"), params)
        out = []
        for r in (j.get("output") or []):
            out.append({
                "order_id": r.get("KRX_FWDG_ORD_ID") or r.get("odno"),
                "symbol": r.get("pdno"),
                "qty": int(_num(r, "ord_qty")),
                "remain": int(_num(r, "rmn_qty", "mslp_qty")),
            })
        return out

    # ── 순위 (깔때기 1단계) ──
    def volume_rank(self, blng_cls="1", market="0000"):
        params = {
            "FID_COND_MRKT_DIV_CODE": "J", "FID_COND_SCR_DIV_CODE": "20171",
            "FID_INPUT_ISCD": market, "FID_DIV_CLS_CODE": "0",
            "FID_BLNG_CLS_CODE": blng_cls, "FID_TRGT_CLS_CODE": "111111111",
            "FID_TRGT_EXLS_CLS_CODE": "0000000000",
            "FID_INPUT_PRICE_1": "", "FID_INPUT_PRICE_2": "",
            "FID_VOL_CNT": "", "FID_INPUT_DATE_1": "",
        }
        j = self._get("/uapi/domestic-stock/v1/quotations/volume-rank",
                      self.cfg.tr_id("vol_rank"), params)
        return self._rank_rows(j)

    def fluctuation_rank(self, market="0000"):
        params = {
            "fid_cond_mrkt_div_code": "J", "fid_cond_scr_div_code": "20170",
            "fid_input_iscd": market, "fid_rank_sort_cls_code": "0",
            "fid_input_cnt_1": "0", "fid_prc_cls_code": "0",
            "fid_input_price_1": "", "fid_input_price_2": "", "fid_vol_cnt": "",
            "fid_trgt_cls_code": "0", "fid_trgt_exls_cls_code": "0",
            "fid_div_cls_code": "0", "fid_rsfl_rate1": "", "fid_rsfl_rate2": "",
        }
        j = self._get("/uapi/domestic-stock/v1/ranking/fluctuation",
                      self.cfg.tr_id("flx_rank"), params)
        return self._rank_rows(j)

    @staticmethod
    def _rank_rows(j):
        out = []
        for row in (j.get("output") or []):
            code = row.get("mksc_shrn_iscd") or row.get("stck_shrn_iscd")
            name = row.get("hts_kor_isnm")
            if code:
                out.append((code, name))
        return out
