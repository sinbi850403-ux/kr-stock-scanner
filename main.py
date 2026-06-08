"""
국내증시 전체 스캐너 (코스피+코스닥 전종목)
== 똑똑한 깔때기(Funnel) 설계 ==
  1단계: KIS 순위 API로 '지금 움직이는' 종목만 추출 (거래증가율/거래대금/상승률 Top30 합집합)
  2단계: 후보만 일봉 가져와 6레이어 컨플루언스 계산
  3단계: 5/6 이상이면 텔레그램 알림 (손절/익절 포함)

KIS 실전투자 REST API + 텔레그램 / Railway 24시간 실행
─────────────────────────────────────────────────────────────
⚠️ KIS 순위 API의 FID_* 파라미터는 계정/문서 버전에 따라 미묘하게 다릅니다.
   본인이 이미 돌리는 KIS 봇의 검증된 호출과 대조해서 필요시 조정하세요.
   (funnel_* 함수만 맞으면 나머지는 그대로 동작합니다.)
"""

import os
import time
import threading
import requests
import pandas as pd
import numpy as np
from datetime import datetime, time as dtime, timedelta
import pytz

KST = pytz.timezone("Asia/Seoul")

# ─────────────────────────────────────────
# 환경변수 (Railway Variables)
# ─────────────────────────────────────────
KIS_APP_KEY    = os.environ["KIS_APP_KEY"]
KIS_APP_SECRET = os.environ["KIS_APP_SECRET"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT  = os.environ["TELEGRAM_CHAT_ID"]

BASE = "https://openapi.koreainvestment.com:9443"

# ─────────────────────────────────────────
# 설정 (Railway Variables로 덮어쓰기 가능)
# ─────────────────────────────────────────
THRESHOLD     = int(os.environ.get("THRESHOLD", "5"))      # 알림 점수 (5 or 6)
TOP_N         = int(os.environ.get("TOP_N", "30"))         # 각 순위 Top N
SCAN_INTERVAL = int(os.environ.get("SCAN_INTERVAL", "600"))# 스캔 주기(초). 600=10분
RATE_PER_SEC  = float(os.environ.get("RATE_PER_SEC", "8")) # 초당 API 호출 상한
                                                           # (다른 봇과 앱키 공유시 낮추세요)
FINAL_SCAN_HHMM = os.environ.get("FINAL_SCAN", "1512")     # 장마감 직전 확정 스캔 시각

# ─────────────────────────────────────────
# 레이트 리미터 (토큰버킷 간이판)
# ─────────────────────────────────────────
_rate_lock = threading.Lock()
_last_call = [0.0]

def _throttle():
    min_gap = 1.0 / RATE_PER_SEC
    with _rate_lock:
        now = time.monotonic()
        wait = _last_call[0] + min_gap - now
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.monotonic()

# ─────────────────────────────────────────
# 토큰 발급 (캐시)
# ─────────────────────────────────────────
_token = {"v": None, "exp": 0.0}

def get_token():
    if _token["v"] and time.time() < _token["exp"]:
        return _token["v"]
    r = requests.post(f"{BASE}/oauth2/tokenP", json={
        "grant_type": "client_credentials",
        "appkey": KIS_APP_KEY,
        "appsecret": KIS_APP_SECRET,
    }, timeout=10)
    r.raise_for_status()
    d = r.json()
    _token["v"] = d["access_token"]
    _token["exp"] = time.time() + int(d.get("expires_in", 86400)) - 120
    return _token["v"]

def kis_get(path: str, tr_id: str, params: dict, retries: int = 2) -> dict:
    """공통 GET 호출: 레이트리밋 + 재시도"""
    headers = {
        "authorization": f"Bearer {get_token()}",
        "appkey": KIS_APP_KEY,
        "appsecret": KIS_APP_SECRET,
        "tr_id": tr_id,
        "custtype": "P",
    }
    for attempt in range(retries + 1):
        _throttle()
        try:
            r = requests.get(f"{BASE}{path}", headers=headers, params=params, timeout=10)
            if r.status_code == 200:
                return r.json()
            # 초당호출초과(EGW00201) 등 → 잠깐 쉬고 재시도
            time.sleep(0.5 * (attempt + 1))
        except requests.RequestException:
            time.sleep(0.5 * (attempt + 1))
    return {}

# ═════════════════════════════════════════
# 1단계 깔때기: 순위 API로 후보 추출
# ═════════════════════════════════════════
def _pick(row: dict, *keys):
    for k in keys:
        if k in row and row[k]:
            return row[k]
    return None

def _is_tradable(code: str, name: str) -> bool:
    """우선주/ETF/ETN/스팩/리츠 등 제외 (단순 휴리스틱)"""
    if not code or len(code) != 6:
        return False
    # 우선주: 보통 코드 끝자리가 0이 아님(5,7,9,K 등)
    if code[-1] != "0":
        return False
    bad = ["우", "스팩", "ETN", "ETF", "리츠", "인버스", "레버리지",
           "KODEX", "TIGER", "ARIRANG", "KBSTAR", "HANARO", "PLUS",
           "ACE", "SOL", "RISE", "TIMEFOLIO"]
    return not any(b in (name or "") for b in bad)

def funnel_volume_rank(blng_cls: str, market: str = "0000") -> list:
    """
    거래량순위 (FHPST01710000)
    blng_cls: '1'=거래증가율 '3'=거래대금순  (소속구분)
    market : '0000'=전체 '0001'=코스피 '1001'=코스닥
    최대 30건
    """
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_COND_SCR_DIV_CODE": "20171",
        "FID_INPUT_ISCD": market,
        "FID_DIV_CLS_CODE": "0",        # 0=전체
        "FID_BLNG_CLS_CODE": blng_cls,  # 소속구분
        "FID_TRGT_CLS_CODE": "111111111",
        "FID_TRGT_EXLS_CLS_CODE": "0000000000",
        "FID_INPUT_PRICE_1": "",
        "FID_INPUT_PRICE_2": "",
        "FID_VOL_CNT": "",
        "FID_INPUT_DATE_1": "",
    }
    j = kis_get("/uapi/domestic-stock/v1/quotations/volume-rank",
                "FHPST01710000", params)
    out = []
    for row in (j.get("output") or [])[:TOP_N]:
        code = _pick(row, "mksc_shrn_iscd", "stck_shrn_iscd")
        name = _pick(row, "hts_kor_isnm")
        if code and _is_tradable(code, name):
            out.append((code, name))
    return out

def funnel_fluctuation(market: str = "0000") -> list:
    """
    등락률순위 (FHPST01700000) — 상승률 상위. 최대 30건
    """
    params = {
        "fid_cond_mrkt_div_code": "J",
        "fid_cond_scr_div_code": "20170",
        "fid_input_iscd": market,
        "fid_rank_sort_cls_code": "0",   # 0=상승률순
        "fid_input_cnt_1": "0",
        "fid_prc_cls_code": "0",
        "fid_input_price_1": "",
        "fid_input_price_2": "",
        "fid_vol_cnt": "",
        "fid_trgt_cls_code": "0",
        "fid_trgt_exls_cls_code": "0",
        "fid_div_cls_code": "0",
        "fid_rsfl_rate1": "",
        "fid_rsfl_rate2": "",
    }
    j = kis_get("/uapi/domestic-stock/v1/ranking/fluctuation",
                "FHPST01700000", params)
    out = []
    for row in (j.get("output") or [])[:TOP_N]:
        code = _pick(row, "stck_shrn_iscd", "mksc_shrn_iscd")
        name = _pick(row, "hts_kor_isnm")
        if code and _is_tradable(code, name):
            out.append((code, name))
    return out

def build_candidates() -> dict:
    """3개 순위 합집합 → {code: name}"""
    cand = {}
    try:
        for code, name in funnel_volume_rank("1"):   # 거래증가율
            cand[code] = name
        for code, name in funnel_volume_rank("3"):   # 거래대금
            cand[code] = name
        for code, name in funnel_fluctuation("0000"):# 상승률
            cand[code] = name
    except Exception as e:
        print(f"[FUNNEL ERROR] {e}")
    return cand

# ═════════════════════════════════════════
# 2단계: 일봉 가져오기 + 지표
# ═════════════════════════════════════════
def get_daily(code: str, count: int = 100) -> pd.DataFrame:
    """국내주식 기간별 일봉 (FHKST03010100), 최대 100봉"""
    end = datetime.now(KST).strftime("%Y%m%d")
    start = (datetime.now(KST) - timedelta(days=count * 2 + 30)).strftime("%Y%m%d")
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": code,
        "FID_INPUT_DATE_1": start,
        "FID_INPUT_DATE_2": end,
        "FID_PERIOD_DIV_CODE": "D",
        "FID_ORG_ADJ_PRC": "0",   # 0=수정주가
    }
    j = kis_get("/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
                "FHKST03010100", params)
    rows = j.get("output2") or []
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    ren = {"stck_bsop_date": "date", "stck_clpr": "close", "stck_oprc": "open",
           "stck_hgpr": "high", "stck_lwpr": "low", "acml_vol": "volume"}
    df.rename(columns={k: v for k, v in ren.items() if k in df.columns}, inplace=True)
    for c in ["open", "high", "low", "close", "volume"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["close"])
    df = df[df["close"] > 0].iloc[::-1].reset_index(drop=True)  # 과거→현재
    return df

def ema(s, n):  return s.ewm(span=n, adjust=False).mean()

def rsi(c, n=14):
    d = c.diff()
    g = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100 / (1 + g / l.replace(0, np.nan))

def macd(c, f=12, s=26, sig=9):
    m = ema(c, f) - ema(c, s)
    sg = ema(m, sig)
    return m, sg, m - sg

def atr(h, l, c, n=14):
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(span=n, adjust=False).mean()

# ═════════════════════════════════════════
# 6레이어 컨플루언스 (일봉 스윙 / EMA 5·20·60)
# ═════════════════════════════════════════
def calc_score(df: pd.DataFrame) -> dict:
    if len(df) < 65:
        return None
    c, h, l, v, o = df["close"], df["high"], df["low"], df["volume"], df["open"]
    e5, e20, e60 = ema(c, 5), ema(c, 20), ema(c, 60)
    r = rsi(c, 14)
    m, sg, hist = macd(c)
    a = atr(h, l, c, 14)
    vavg = v.rolling(20).mean()
    rvol = (v / vavg.replace(0, np.nan))

    price = float(c.iloc[-1])
    cur_atr = float(a.iloc[-1])

    # L1: 단기 정배열 (5>20>60)
    L1 = bool(e5.iloc[-1] > e20.iloc[-1] > e60.iloc[-1])
    # L2: 장기추세 상승 (MTF 대용: 60일선 위 + 20>60)
    L2 = bool(price > e60.iloc[-1] and e20.iloc[-1] > e60.iloc[-1])
    # L3: 피보 황금구간(최근 60봉) OR 20일선 지지(이격 -2%~+1.5%)
    rec = df.tail(60)
    hi, lo = rec["high"].max(), rec["low"].min()
    rng = hi - lo
    gz_top, gz_bot = hi - rng * 0.382, hi - rng * 0.618
    in_gz = bool(gz_bot <= price <= gz_top)
    near_ma20 = bool(-0.02 <= (price - e20.iloc[-1]) / e20.iloc[-1] <= 0.015)
    L3 = in_gz or near_ma20
    # L4: RSI 45 상향돌파 OR MACD 골든크로스+히스토 증가
    rsi_x = bool(r.iloc[-2] < 45 <= r.iloc[-1])
    macd_x = bool(m.iloc[-2] < sg.iloc[-2] and m.iloc[-1] >= sg.iloc[-1] and hist.iloc[-1] > hist.iloc[-2])
    L4 = rsi_x or macd_x
    # L5: 거래량 급증(1.5x) + 양봉
    L5 = bool((rvol.iloc[-1] >= 1.5) and (c.iloc[-1] >= o.iloc[-1]))
    # L6: 20일 신고가 돌파 (BOS)
    prev_hi = float(h.iloc[-21:-1].max())
    L6 = bool(price > prev_hi)

    # 갭 필터 (일봉: 5% 이상 갭상승이면 추격 차단)
    gap = abs(o.iloc[-1] - c.iloc[-2]) / c.iloc[-2] * 100 if c.iloc[-2] else 0
    gap_ok = gap <= 5.0

    score = int(L1 + L2 + L3 + L4 + L5 + L6)
    sl = price - cur_atr * 2.0
    tp = price + (price - sl) * 2.0
    return {
        "score": score,
        "layers": {"L1": L1, "L2": L2, "L3": L3, "L4": L4, "L5": L5, "L6": L6},
        "price": price, "sl": round(sl), "tp": round(tp),
        "rvol": round(float(rvol.iloc[-1]), 1) if not np.isnan(rvol.iloc[-1]) else 0,
        "gap_ok": gap_ok,
        "chg": round((price / c.iloc[-2] - 1) * 100, 2) if c.iloc[-2] else 0,
    }

# ═════════════════════════════════════════
# 3단계: 텔레그램
# ═════════════════════════════════════════
def send(msg: str):
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                      json={"chat_id": TELEGRAM_CHAT, "text": msg, "parse_mode": "HTML"},
                      timeout=10)
    except Exception as e:
        print(f"[TG ERROR] {e}")

def fmt(code, name, res, provisional):
    L = res["layers"]
    chk = "".join(
        f"\n  {k} {'✅' if L[k] else '❌'}"
        for k in ["L1", "L2", "L3", "L4", "L5", "L6"]
    )
    tier = "🔥 FULL(6/6)" if res["score"] == 6 else f"⭐ {res['score']}/6"
    tag = "  <i>(장중 잠정)</i>" if provisional else "  <b>(마감확정)</b>"
    now = datetime.now(KST).strftime("%m/%d %H:%M")
    return (f"📈 <b>[{name}] {code}</b> 매수신호{tag}\n"
            f"등급: {tier}  ({res['chg']:+.1f}%)\n"
            f"현재가: {res['price']:,}원\n"
            f"손절: {res['sl']:,}원 / 익절: {res['tp']:,}원\n"
            f"거래량: {res['rvol']}x{chk}\n⏰ {now}")

# ═════════════════════════════════════════
# 장 운영 / 스케줄 / 중복방지
# ═════════════════════════════════════════
def market_open() -> bool:
    now = datetime.now(KST)
    if now.weekday() >= 5:
        return False
    return dtime(9, 10) <= now.time() <= dtime(15, 20)

_alerted = set()   # {f"{code}_{yyyymmdd}"}

def already(code):
    return f"{code}_{datetime.now(KST):%Y%m%d}" in _alerted

def mark(code):
    _alerted.add(f"{code}_{datetime.now(KST):%Y%m%d}")

def is_final_scan() -> bool:
    return datetime.now(KST).strftime("%H%M") >= FINAL_SCAN_HHMM

# ═════════════════════════════════════════
# 메인 스캔
# ═════════════════════════════════════════
def scan_once():
    t0 = time.time()
    cand = build_candidates()
    provisional = not is_final_scan()
    hits = 0
    for code, name in cand.items():
        if already(code):
            continue
        try:
            df = get_daily(code)
            res = calc_score(df)
            if res and res["score"] >= THRESHOLD and res["gap_ok"]:
                send(fmt(code, name, res, provisional))
                mark(code)
                hits += 1
        except Exception as e:
            print(f"[CALC ERROR] {code}: {e}")
    print(f"[{datetime.now(KST):%H:%M}] 후보 {len(cand)} / 알림 {hits} "
          f"/ {time.time()-t0:.1f}s")

def main():
    print("국내증시 전체 스캐너 시작")
    send(f"🤖 국내증시 전체 스캐너 ON\n"
         f"기준: 일봉 {THRESHOLD}/6↑ / 주기 {SCAN_INTERVAL//60}분 / 확정 {FINAL_SCAN_HHMM}")
    while True:
        try:
            if market_open():
                scan_once()
            else:
                # 장 외에는 다음날 위해 알림기록 초기화 (자정 지나면)
                if datetime.now(KST).strftime("%H%M") == "0600":
                    _alerted.clear()
        except Exception as e:
            print(f"[LOOP ERROR] {e}")
        time.sleep(SCAN_INTERVAL)

if __name__ == "__main__":
    main()
