# 국내주식 컨플루언스 PRO — 스캐너 + KIS 자동매매

코스피·코스닥 종목을 **6레이어 컨플루언스**로 분석해 텔레그램 알림 + **KIS 자동주문**까지 수행합니다.
TradingView Pine 지표 "국내주식 컨플루언스 PRO"의 일봉 스윙 로직을 그대로 포팅했습니다.

> ⚠️ **모의투자가 기본값**입니다. 실거래는 명시적 3중 플래그로만 켜집니다.
> 투자 판단·매매 책임은 본인에게 있습니다.

## 설계 문서
- 전체 설계: [docs/DESIGN.md](docs/DESIGN.md)
- 기준 스펙(Pine): [docs/reference/confluence_pro_v5.pine](docs/reference/confluence_pro_v5.pine)

## 핵심 안전 설계 — 비반복 2단계

미확정 일봉으로 주문이 발동해 신호가 뒤집히는 사고를 구조적으로 차단합니다.

```
장중(09:10~15:20)  → 잠정 알림만 (주문 X)
마감 후(15:40~)    → 확정 일봉으로 신호 산출 → 익일 진입 후보 저장
익일 개장(09:10~)  → 게이트 재검증 → 실제 매수
```

## 6레이어 컨플루언스 (일봉)

| L | 조건 |
|---|------|
| L1 | 3EMA 정배열 (5>20>60, close>60) |
| L2 | 실제 MTF — 일(EMA+MACD) · 주봉 · 월봉 정렬 |
| L3 | 피보 황금구간(0.5~0.618) 또는 오더블록 내부 |
| L4 | RSI 45 상향돌파 또는 MACD 골든크로스 |
| L5 | 거래량 3증거(rvol·OBV·VWAP) 2-of-3 + 분배 베토 |
| L6 | BOS/CHoCH 구조 상태머신 (강세 확정) |

`score ≥ THRESHOLD`(기본 5) → 신호.

## 자동주문

진입(시장가) → TP 3분할 지정가 대기 → TP 체결 시 추적 SL → SL/역신호 시 청산.
OCO 미지원이라 TP는 거래소 상주 지정가, SL은 소프트웨어 감시(현재가 폴링) 후 시장가 청산.

안전장치: 모의투자 기본 / 일손실 킬스위치 / 포지션·거래횟수 한도 / 갭·VI·상한가 게이트 / 재시작 시 KIS 잔고 동기화.

## 모듈 구조

```
config.py       설정·모의/실전 전환
indicators.py   EMA/ATR/RSI/MACD/OBV/VWAP        (순수)
structure.py    피봇/피보/오더블록/BOS·CHoCH      (순수)
confluence.py   L1~L6 점수                        (순수)
gates.py        갭/VI/상하한/시간/한도/중복       (순수)
risk.py         사이징/호가단위/추적SL            (순수)
strategy.py     신호 생성/역신호                  (순수)
kis_client.py   KIS API 래퍼
scanner.py      깔때기→멀티종목 스캔
trader.py       자동주문 엔진
notify.py       텔레그램
main.py         상태머신 + 재시작복구 + 킬스위치
```

## 환경변수

| 변수 | 설명 |
|------|------|
| `KIS_APP_KEY` / `KIS_APP_SECRET` | 한국투자증권 앱키/시크릿 |
| `KIS_ACCOUNT` | 계좌번호 `12345678-01` |
| `KIS_ACCOUNT_PWD` | 계좌 비밀번호 (실거래 시 필수) |
| `TELEGRAM_TOKEN` / `TELEGRAM_CHAT_ID` | 텔레그램 봇 |
| `THRESHOLD` | 신호 점수 (기본 5) |
| `RISK_PCT` | 1회 리스크 비율 (기본 0.01) |
| `RATE_PER_SEC` | 초당 API 호출 상한 (기본 8) |
| `MAX_POSITIONS` / `MAX_DAILY_TRADES` | 동시 포지션 / 일일 거래 한도 |
| `DAILY_MAX_LOSS_PCT` | 킬스위치 일손실 한도 (기본 0.03) |
| `ENABLE_REAL_TRADING` | 실거래 활성화 1단계 (`1`) |
| `REAL_TRADING_CONFIRM` | 실거래 활성화 2단계 (`YES`) |

> 실거래는 `ENABLE_REAL_TRADING=1` **그리고** `REAL_TRADING_CONFIRM=YES` 둘 다 설정해야 켜집니다. 그 외엔 항상 모의투자.

## 실행

```bash
pip install -r requirements.txt
python main.py
```

## 테스트

```bash
pytest                       # 전체 (190개)
pytest --cov=. --cov-report=term-missing
```

모든 코드는 테스트 우선으로 작성됐습니다 (순수 모듈 95~100% 커버리지).

## 미검증 — 모의투자로 우선 확인 (docs/DESIGN.md §10)

호가단위 자동보정 여부, `ORD_DVSN` 정확값, 계좌비번 암호화, VI/상한가 주문 거부, 잔고/미체결 TR 등은 실거래 전 **모의투자로 반드시 실측 검증**하세요.
