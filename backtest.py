"""
백테스트 모듈 — 로컬 실행용 (pykrx 지연 임포트).

CLI: python backtest.py 005930 000660 ... (또는 --file symbols.txt)
데이터: pykrx 일봉 3년 조회 (지연 임포트 — 메인 봇 영향 없음).

로직:
  - 워크포워드: t까지의 데이터만 사용해 신호 산출.
  - 진입: score >= threshold AND ext_pct <= max_ext_pct → 다음날 시가 진입.
  - SL: ATR*sl_atr_mult, TP: 설정된 R배율.
  - 보유: EMA 역배열 발생 또는 SL/TP 터치 시 청산 (같은 캔들: SL 우선).
  - 비용: 매도 시 0.18% 세금 + 0.03% 수수료.
출력: 거래 목록 (backtest_trades.csv) + 콘솔 요약.
"""
import sys
import csv
import logging
from datetime import datetime
from typing import List, Optional, Callable, Dict, Any

import pandas as pd
import pytz

from config import Config
import confluence as cf
import strategy as st
import indicators as ind
import risk

log = logging.getLogger(__name__)
KST = pytz.timezone("Asia/Seoul")

# 비용 (매도 시): 세금 0.18% + 수수료 0.03%
COST_RATE = 0.0021


def fetch_daily_data(symbol: str) -> pd.DataFrame:
    """pykrx에서 일봉 3년 조회 (지연 임포트)."""
    try:
        from pykrx import stock
    except ImportError:
        raise ImportError("pykrx 라이브러리 필요: pip install pykrx")

    try:
        df = stock.get_market_ohlcv_by_date(fromdate="20230101", todate="20261231", ticker=symbol)
        # pykrx 컬럼명: Open, High, Low, Close, Volume
        df = df.reset_index()
        df.columns = ["date", "open", "high", "low", "close", "volume"]
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y%m%d")
        return df
    except Exception as e:
        log.error("pykrx 조회 실패 (%s): %s", symbol, e)
        return pd.DataFrame()


def resample_to_weekly(df: pd.DataFrame) -> pd.DataFrame:
    """일봉 → 주봉 (pandas resample)."""
    if df.empty or len(df) < 7:
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")
    weekly = df.resample("W").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna()
    return weekly.reset_index().rename(columns={"date": "date"})


def resample_to_monthly(df: pd.DataFrame) -> pd.DataFrame:
    """일봉 → 월봉 (pandas resample)."""
    if df.empty or len(df) < 30:
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")
    monthly = df.resample("ME").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna()
    return monthly.reset_index().rename(columns={"date": "date"})


def run_backtest(symbols: List[str], cfg: Config,
                 fetch: Optional[Callable] = None) -> List[Dict[str, Any]]:
    """백테스트 실행.

    Args:
        symbols: 종목코드 리스트.
        cfg: Config 객체.
        fetch: 테스트용 fetch 함수 (None이면 pykrx 사용).

    Returns:
        거래 리스트 (각 거래: entry_date, entry_price, exit_date, exit_price, qty, pnl, R).
    """
    if fetch is None:
        fetch = fetch_daily_data

    all_trades = []

    for symbol in symbols:
        log.info("백테스트: %s", symbol)
        df_d = fetch(symbol)
        if df_d.empty or len(df_d) < cfg.ema_slow + 5:
            log.warning("데이터 부족: %s", symbol)
            continue

        df_d = df_d.reset_index(drop=True)
        df_d["date"] = pd.to_datetime(df_d["date"])

        df_w = resample_to_weekly(df_d)
        df_m = resample_to_monthly(df_d)

        trades = _simulate_symbol(symbol, df_d, df_w, df_m, cfg)
        all_trades.extend(trades)

    # 결과 출력
    _print_summary(all_trades)
    _write_csv(all_trades)

    return all_trades


def _simulate_symbol(symbol: str, df_d: pd.DataFrame, df_w: pd.DataFrame, df_m: pd.DataFrame,
                     cfg: Config) -> List[Dict[str, Any]]:
    """단일 종목 시뮬."""
    trades = []
    position = None  # {"entry_date": "...", "entry_price": ..., "qty": ..., "entry_idx": ...}
    tp1_done = False  # TP1 체결 후 SL 상향 추적 여부

    for t in range(cfg.ema_slow + 5, len(df_d)):
        # t까지의 데이터만 추출 (룩어헤드 방지)
        df_t = df_d.iloc[:t+1].reset_index(drop=True)
        df_w_t = df_w[df_w["date"] <= df_d.iloc[t]["date"]] if not df_w.empty else pd.DataFrame()
        df_m_t = df_m[df_m["date"] <= df_d.iloc[t]["date"]] if not df_m.empty else pd.DataFrame()

        date_t = df_d.iloc[t]["date"].strftime("%Y%m%d")
        price_t = float(df_d.iloc[t]["close"])
        high_t = float(df_d.iloc[t]["high"])
        low_t = float(df_d.iloc[t]["low"])
        open_t = float(df_d.iloc[t]["open"])

        # ── 보유 중: SL/TP/역신호 확인 ──
        if position:
            entry_price = position["entry_price"]
            sl_price = position["sl_price"]
            tp1_price = position["tp1_price"]
            tp2_price = position["tp2_price"]
            tp3_price = position["tp3_price"]

            # EMA 역배열 체크 (매일)
            c = df_t["close"]
            e5 = ind.ema(c, cfg.ema_fast).iloc[-1]
            e20 = ind.ema(c, cfg.ema_mid).iloc[-1]
            e60 = ind.ema(c, cfg.ema_slow).iloc[-1]
            ema_reverse = (e5 < e20 < e60)

            if ema_reverse:
                # 그날 종가 청산
                pnl = (price_t - entry_price) * position["qty"] * (1 - COST_RATE)
                pnl_r = pnl / position["risk_krw"] if position["risk_krw"] > 0 else 0
                trades.append({
                    "symbol": symbol,
                    "entry_date": position["entry_date"],
                    "entry_price": entry_price,
                    "exit_date": date_t,
                    "exit_price": price_t,
                    "qty": position["qty"],
                    "pnl": pnl,
                    "pnl_r": pnl_r,
                    "reason": "EMA 역배열",
                })
                position = None
                continue

            # 같은 캔들에서 SL과 TP 모두 터치 시 SL 우선 (보수적)
            sl_hit = low_t <= sl_price <= high_t
            tp_hit = low_t <= tp1_price <= high_t or \
                     low_t <= tp2_price <= high_t or \
                     low_t <= tp3_price <= high_t

            if sl_hit and tp_hit:
                # SL 우선
                pnl = (sl_price - entry_price) * position["qty"] * (1 - COST_RATE)
                pnl_r = pnl / position["risk_krw"] if position["risk_krw"] > 0 else 0
                trades.append({
                    "symbol": symbol,
                    "entry_date": position["entry_date"],
                    "entry_price": entry_price,
                    "exit_date": date_t,
                    "exit_price": sl_price,
                    "qty": position["qty"],
                    "pnl": pnl,
                    "pnl_r": pnl_r,
                    "reason": "SL",
                })
                position = None
                continue

            # TP 체결
            if tp_hit:
                if not tp1_done and low_t <= tp1_price <= high_t:
                    # TP1 체결 → SL을 entry+0.5R로 상향
                    tp1_done = True
                    be_price = entry_price + position["risk_krw"] / (position["qty"] * 0.5)
                    position["sl_price"] = be_price
                    continue
                elif tp1_done:
                    # TP2/3 중 하나 체결 → 청산
                    exit_p = price_t  # 간단히 종가로 처리
                    pnl = (exit_p - entry_price) * position["qty"] * (1 - COST_RATE)
                    pnl_r = pnl / position["risk_krw"] if position["risk_krw"] > 0 else 0
                    trades.append({
                        "symbol": symbol,
                        "entry_date": position["entry_date"],
                        "entry_price": entry_price,
                        "exit_date": date_t,
                        "exit_price": exit_p,
                        "qty": position["qty"],
                        "pnl": pnl,
                        "pnl_r": pnl_r,
                        "reason": "TP",
                    })
                    position = None
                    tp1_done = False
                    continue

            # SL 터치
            if sl_hit:
                pnl = (sl_price - entry_price) * position["qty"] * (1 - COST_RATE)
                pnl_r = pnl / position["risk_krw"] if position["risk_krw"] > 0 else 0
                trades.append({
                    "symbol": symbol,
                    "entry_date": position["entry_date"],
                    "entry_price": entry_price,
                    "exit_date": date_t,
                    "exit_price": sl_price,
                    "qty": position["qty"],
                    "pnl": pnl,
                    "pnl_r": pnl_r,
                    "reason": "SL",
                })
                position = None
                tp1_done = False
                continue

        # ── 신호 체크 (미보유) ──
        if not position:
            res = cf.full(df_t, df_w_t, df_m_t, cfg)
            if res and res.score >= cfg.threshold and res.ext_pct <= cfg.max_ext_pct:
                # 다음날 시가 진입
                if t + 1 < len(df_d):
                    next_open = float(df_d.iloc[t+1]["open"])
                    sl_price = risk.compute_stop(next_open, res.atr, cfg, ob_bot=res.ob_bot)
                    if sl_price < next_open:
                        # 위험도 기반 수량
                        risk_krw = 100_000  # 고정 리스크 (실제로는 cfg.risk_pct * account)
                        qty = max(1, int(risk_krw / abs(next_open - sl_price)))

                        # TP 계산
                        atr_val = res.atr
                        tp1 = next_open + (next_open - sl_price) * cfg.tp1_r
                        tp2 = next_open + (next_open - sl_price) * cfg.tp2_r
                        tp3 = next_open + (next_open - sl_price) * cfg.tp3_r

                        position = {
                            "entry_date": df_d.iloc[t+1]["date"].strftime("%Y%m%d"),
                            "entry_price": next_open,
                            "qty": qty,
                            "sl_price": sl_price,
                            "tp1_price": tp1,
                            "tp2_price": tp2,
                            "tp3_price": tp3,
                            "risk_krw": risk_krw,
                            "entry_idx": t + 1,
                        }
                        tp1_done = False

    return trades


def _print_summary(trades: List[Dict[str, Any]]) -> None:
    """콘솔 요약 출력."""
    if not trades:
        print("거래 없음")
        return

    total = len(trades)
    wins = sum(1 for t in trades if t.get("pnl", 0) > 0)
    winrate = wins / total if total > 0 else 0
    avg_r = sum(t.get("pnl_r", 0) for t in trades) / total if total > 0 else 0
    max_loss = min(t.get("pnl", 0) for t in trades) if trades else 0

    print(f"\n=== 백테스트 결과 ===")
    print(f"총 거래: {total}")
    print(f"승률: {winrate*100:.1f}%")
    print(f"평균 R: {avg_r:.2f}R")
    print(f"최대 손실: {max_loss:,.0f}원")


def _write_csv(trades: List[Dict[str, Any]], path: str = "backtest_trades.csv") -> None:
    """CSV로 거래 기록."""
    if not trades:
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=trades[0].keys())
        writer.writeheader()
        writer.writerows(trades)
    log.info("거래 기록: %s", path)


def main():
    """CLI 진입점."""
    if len(sys.argv) < 2:
        print("사용법: python backtest.py 005930 000660 ... (또는 --file symbols.txt)")
        sys.exit(1)

    symbols = []
    if sys.argv[1] == "--file":
        if len(sys.argv) < 3:
            print("--file symbols.txt 형식")
            sys.exit(1)
        with open(sys.argv[2]) as f:
            symbols = [line.strip() for line in f if line.strip()]
    else:
        symbols = sys.argv[1:]

    cfg = Config.from_env()
    run_backtest(symbols, cfg)


if __name__ == "__main__":
    main()
