# 국내주식 컨플루언스 PRO — 일봉 스캐너 + KIS 자동주문 설계서

> **Doc-First 문서** — 이 설계가 확정된 후에야 코드 작성을 시작합니다.
> 기준 스펙(SOURCE OF TRUTH): [`docs/reference/confluence_pro_v5.pine`](reference/confluence_pro_v5.pine)
> 작성: 2026-06-09 / 최종 업데이트: 2026-06-11 / 상태: **확정·구현 완료** (15개 모듈 / pytest 252 통과 / 커버리지 96%+)

---

## 0. 목표 & 범위

현재 `main.py`(단순 일봉판 6레이어 스캐너)를 다음으로 고도화한다.

1. **6레이어를 Pine 수준으로 정교화** — 실제 MTF(일/주/월봉), 오더블록, OBV/VWAP/분배, BOS/CHoCH 상태머신, KR 게이트
2. **텔레그램 알림 + KIS 자동주문** — 신호 발생 시 실제 매수/관리까지
3. **모의투자(Paper) 기본** — 실거래는 명시적 3중 플래그로만 활성화

### 확정된 제약 (사용자 결정)
- **타임프레임**: 일봉 스윙 (실행=일봉 `D`, 상위확인=주봉 `W`/월봉 `M`)
- **시장**: 코스피 + 코스닥 전체 (기존 깔때기 유지)
- **동작**: 알림 + KIS 자동주문 (모의투자 우선)
- **원칙**: Doc-First(설계 확정 후 코드), 테스트 우선(테스트 작성→통과 후 적용)

---

## 1. 핵심 안전 설계 — 비반복(Non-Repainting) 2단계 플로우

> 검증에서 가장 큰 위험으로 지적된 것: **미확정 일봉으로 자동주문이 발동하면, 장 마감 후 신호가 뒤집혀도 주문은 되돌릴 수 없다.**

이를 구조적으로 차단한다. **알림과 주문을 분리**한다.

```
[장중] 잠정(provisional) 알림만        — 정보용. 주문 발동 안 함.
[장마감 후] 확정 일봉으로 신호 산출     — 다음날 진입 후보 확정.
[다음날 개장 후] 게이트 재검증 → 진입    — 확정 신호에만 실제 주문.
```

| 단계 | 시각(KST) | 동작 |
|------|-----------|------|
| **A. 장중 스캔** | 09:10–15:20 | 깔때기→후보→6레이어 계산. `THRESHOLD` 이상 시 **잠정 알림만** (주문 X) |
| **B. 확정 산출** | 15:40 이후 | 당일 확정 일봉으로 재계산 → 진입후보 `pending_signals` 저장(디스크) |
| **C. 진입 실행** | 익일 09:10–09:30 | `pending_signals` 중 최고점 → 게이트 재검증(갭/VI/한도) → **실제 매수** |
| **D. 보유 관리** | 09:30–15:20 | 30초 주기로 SL/TP 감시·추적, 역신호 청산 |
| **E. 일일 리셋** | 16:00 | 미체결 정리, 카운터 리셋, 상태 저장 |

→ 자동주문은 **확정 일봉 + 다음 세션 게이트 통과** 시에만 발동. repaint 불가능.

---

## 2. 모듈 구조 (단일 main.py → 테스트 가능한 12 모듈)

| 파일 | 책임 | 순수성 |
|------|------|--------|
| `config.py` | 설정·환경변수, `is_paper` 기본 True | 순수 |
| `kis_client.py` | KIS API 래퍼 (캔들/현재가/주문/잔고/체결) | I/O (mock 테스트) |
| `indicators.py` | EMA/ATR/RSI/MACD/OBV/VWAP대체 — pandas | **순수** |
| `structure.py` | 스윙피봇·피보황금구간·오더블록·BOS/CHoCH | **순수** |
| `confluence.py` | L1~L6 점수 계산 (핵심) | **순수** |
| `gates.py` | 갭/VI/상한가/거래시간/한도/중복 게이트 | **순수** |
| `risk.py` | 사이징·SL/TP·호가단위·추적SL | **순수** |
| `strategy.py` | 신호 생성 (confluence→Signal) | **순수** |
| `scanner.py` | 깔때기 후보→멀티종목 신호 수집 | I/O |
| `trader.py` | 자동주문 엔진 (진입/TP/SL/청산) | I/O |
| `notify.py` | 텔레그램 알림 포맷·발송 | I/O |
| `main.py` | 상태머신 메인 루프 + 재시작 복구 | 조립 |

순수 모듈(7개)은 외부 의존 없이 단위테스트 100% 목표. I/O 모듈은 KIS 응답 mock으로 검증.

---

## 2.5 2026-06-11 업데이트 — L7 제거 & 신규 기능

### L7(기관/외국인) 계층 제거 (6레이어 → 6레이어)
- **사유**: 당일 순매수 부호만으로는 수급 판단 신뢰도 낮음 (매매 시기 불명확).
- **변경**: 총 점수 7점 → 6점 (threshold 기본값 5 유지 = 5/6 의미).
- **영향**: config.py `_TR_MAP`의 "investor" 항목, kis_client.py `get_investor_flow()` 제거.
- **테스트**: test_confluence.py L7 관련 12개 → 0개 (전부 삭제), test_notify.py "5/7" → "5/6" 변경.

### 역신호(Counter Exit) 체크 3회/일 (1회 → 3회)
- **기존**: 날짜 기반 (일 1회만 체크).
- **신규**: 슬롯 방식 → AM/MD/PM 3회 독립 체크.
  - AM: hhmm < "1200"
  - MD: "1200" <= hhmm < "1400"  
  - PM: 그 외
- **상태 직렬화**: `counter_checked` set → list → set 라운드트립.
- **테스트**: test_main.py 7개 신규 슬롯 테스트 추가.

### 신호 전수 기록 (Forward-Test 표본)
- **신규 모듈**: `siglog.py` (log_signal 함수).
- **출력**: 
  - JSONL append (signals_log.jsonl) — 파일 쓰기 실패 무시.
  - logging "SIGLOG {json}" — Railway 로그에 영속.
- **필드**: ts, date, phase, symbol, name, score, layers, entry, sl, ext_pct.
- **호출**: main.py 3곳 (_intraday_alert_scan, _post_close_scan, _evening_scan) 알림 후.
- **테스트**: test_siglog.py 9개 테스트 (파일 쓰기, encoding, logging, 필드).

### 백테스트 모듈 (로컬 검증)
- **신규 파일**: `backtest.py` (CLI & API).
- **데이터**: pykrx 일봉 3년 (지연 임포트 — 메인 무영향).
- **시뮬**: 워크포워드 + 룩어헤드 금지.
  - 진입: 다음날 시가.
  - SL: ATR 기반, TP: R배율.
  - 역신호: EMA 역배열 청산.
  - 비용: 매도 0.18%(세금) + 0.03%(수수료).
- **출력**: CSV (backtest_trades.csv) + 콘솔 요약.
- **문서**: `docs/BACKTEST.md` (사용법 & 가정).
- **테스트**: test_backtest.py 12개 (합성 데이터, 진입/청산, 상태).

### Go-Live 기준 문서
- **신규 파일**: `docs/GOLIVE.md`.
- **체크리스트**: 7개 항목 (모의 30거래, 승률≥50%, 평균R≥0.5, MDD≤5%, 킬스위치무, 안정성, 주문속도).
- **전환 절차**: 2단계 환경변수 + 모니터링.

### 의존성
- **메인 (requirements.txt)**: 변경 없음.
- **개발용 (requirements-dev.txt)**: `pykrx>=0.3.8` 추가.

---

## 3. 6레이어 최종 포트 매핑 (일봉 기준)

> Pine SOURCE OF TRUTH 기준. 현재 `main.py` 대비 **굵게** 표시한 부분이 신규/수정.

| L | 의미 | 일봉 계산식 | 현재 대비 |
|---|------|-------------|-----------|
| **L1** | 3EMA 정배열 | `e5 > e20 > e60 AND close > e60` | 거의 동일 |
| **L2** | 실제 MTF | `D:(e20>e60 & macd>0)` **AND** `W:(e20>e60)` **AND** `M:(e20>e60)` | **가짜MTF→주/월봉 실제 정렬** |
| **L3** | 황금구간/OB | `inGoldenZone(0.5~0.618)` **OR** `price ∈ 활성 불리시 OB` | **OB 감지 신규** |
| **L4** | 모멘텀 | `RSI 45 상향돌파` OR `(MACD 골든크로스 & macd>0 & hist↑)` | 동일 |
| **L5** | 거래량 3증거 | `(volSurge≥1.7) + (OBV↑) + (VWAP대체↑) ≥ 2` AND `not 분배` AND `양봉` | **OBV/VWAP/분배 신규** |
| **L6** | 구조 상태머신 | `os == 1` (BOS 강세 확정) | **20일신고가→BOS/CHoCH** |

**합계 `buyScore` (0~6) ≥ `THRESHOLD`(기본 5) → 신호.**

### 세부 사양
- **EMA**: fast=5, mid=20, slow=60 (일봉). 주/월봉도 동일 비율 e20/e60.
- **L2 MTF**: 주봉 52개, 월봉 24개 캐싱. 주/월봉은 `e20>e60`만(매크로 추세), 일봉은 EMA+MACD.
- **L3 오더블록**: `pivLen=12` 스윙 → 피보 `f382/f500/f618/f786`. GZ = `[min(f500,f618), max(f500,f618)]`. OB = 변위봉(`|close-open|>atr*1.2 & rvol≥1.7 & close>high[-1]` + 직전 음봉) 저장·미티게이션.
- **L5 VWAP 대체**: 일봉엔 세션 VWAP 없음 → **20일 롤링 VWAP** `Σ(hlc3·vol)/Σ(vol)` 사용. `vwapBull = close > vwap20`.
- **L5 분배 베토**: `rvol≥2.0 AND 몸통<레인지*0.2 AND close≥최근10봉고가*0.99` → 신호 무효.
- **L6 상태머신**: 일봉 피봇 돌파로 `os` 갱신. `crossUpPH→os=1`, `crossDnPL→os=-1`. `bearCHoCH=(os_prev==1 & os==-1)` → 청산 트리거.

---

## 4. 자동주문 & 포지션 상태머신

### 4.1 진입 (단계 C)
```
1. pending_signals 최고점 선택
2. 현재가 vs 신호가 괴리 > 5% → 만료, 다음 신호
3. gates.validate() 재검증 (갭/VI/상한가/시간/한도/중복)
4. risk.calc_trade(): qty = (잔고×risk_pct) / (entry−SL), 호가단위 반올림, 3분할
5. 시장가 매수 → entry 체결 확인 (잔고조회로 검증)
6. TP1/TP2/TP3 지정가 매도 대기주문 배치 (qty1/qty2/qty3)
7. entry_info 저장(디스크) → 텔레그램 진입 알림
```

### 4.2 SL/TP 관리 (OCO 미지원 대응)
KIS는 OCO/네이티브 스탑이 없으므로:
- **TP**: 지정가 매도 대기주문 3개 → 거래소에 상주, 봇 없어도 체결
- **SL**: **소프트웨어 감시** — 30초 폴링, `현재가 ≤ SL` 시 시장가 매도 + TP주문 취소
- ⚠️ **갭 리스크**: 야간 갭하락 시 SL보다 낮게 체결될 수 있음(불가피, 명시)

### 4.3 추적 SL (tp_count 기반)
| 상태 | SL 위치 |
|------|---------|
| 진입 직후 (tp_count=0) | `entry − atr×2.0` (또는 OB하단, 더 가까운 쪽) |
| TP1 체결 (1) | `entry + 0.5R` (본전+α) |
| TP2 체결 (2) | `entry + 1.5R` (이익확보) |
| TP3 체결 (3) | 완전청산 |

추가 청산: `역신호(일봉 방향 반전)` 또는 `bearCHoCH` 또는 `EMA 역배열` → 즉시 시장가 청산.
**역신호는 일봉 기준 1일 1회만 체크** (장중 15분봉 변동으로 청산/재진입 반복 방지).

### 4.4 메인 상태머신 (60초 루프)
```
State 0 청산감지  : KIS포지션=None & entry_info=None → 1
State 1 부분청산  : TP 체결 감지 → tp_count++ → SL갱신
State 2 신호스캔  : 포지션 없음 → 깔때기 스캔 (단계 A/C)
State 3 보유관리  : SL/TP 감시, 역신호(1일1회)
```

### 4.5 재시작 복구 (startup_recovery)
봇 크래시/재배포 시 **KIS 잔고가 진실의 원천**:
- 저장상태 로드 → `kis.get_balance()` 실제 포지션 조회 → 대조
- 포지션 없음 → 청산완료 처리 / 수량 동일 → 그대로 복구 / 수량 감소 → 부분체결로 tp_count 재계산 / 수량 증가 → 이상알림 후 수동확인

---

## 5. 리스크 사이징

```
risk_distance = entry − SL
risk_krw      = 잔고 × risk_pct (기본 1%)
qty           = floor(risk_krw / risk_distance)  → 호가단위 반올림
qty1=qty//3, qty2=qty//3, qty3=qty−qty1−qty2     (3분할)
TP1=entry+0.8R, TP2=entry+1.5R, TP3=entry+2.5R   (R=risk_distance)
```
**호가단위(클라이언트 처리)**: KRX 가격대별 틱(예: 1천원↑=1원/5천원↑=5원/1만원↑=10원/5만원↑=50원/10만원↑=100원 … 2023 개편 기준 검증 필요). 모든 주문가는 틱 반올림.

---

## 6. 안전장치 (Safety Guards)

| # | 장치 | 기본값 |
|---|------|--------|
| 1 | **모의투자 기본** | `is_paper=True`. 실거래는 `ENABLE_REAL_TRADING=1` **AND** `REAL_TRADING_CONFIRM=YES` 동시 필요 |
| 2 | 일일 손실 킬스위치 | 누적손실 ≥ 잔고×3% → 전량청산·거래중단 |
| 3 | 동시 포지션 한도 | `max_positions=1` |
| 4 | 일일 거래횟수 | `max_daily_trades=3` |
| 5 | 갭 필터 | 시가갭 > 3% → 스킵 |
| 6 | VI 경고 / 상한가 베토 | |movePct|≥9% 경고, >27% 진입금지 |
| 7 | 거래시간 게이트 | 09:10 이전·15:20 이후 진입금지, 공휴일 제외 |
| 8 | 중복진입 방지 | 동일종목 1시간 내 재진입 차단 + signal_cache(30분 TTL) |
| 9 | 미체결 일일정리 | 16:00 잔여 대기주문 취소 |
| 10 | 상태 영속화 | 매 변경 시 `state_recovery.json` 저장 |

---

## 7. KIS API 매핑 (조사 완료)

| 용도 | 경로 | TR(실전/모의) |
|------|------|---------------|
| 일/주/월봉 | `/quotations/inquire-daily-itemchartprice` | `FHKST03010100` (`FID_PERIOD_DIV_CODE` D/W/M) |
| 현재가 | `/quotations/inquire-price` | `FHKST01010100` (VI·상한·하한 필드 포함) |
| 거래량순위 | `/quotations/volume-rank` | `FHPST01710000` |
| 등락률순위 | `/ranking/fluctuation` | `FHPST01700000` |
| 매수 | `/trading/order-cash` | `TTTC0802U` / `VTTC0802U` |
| 매도 | `/trading/order-cash` | `TTTC0801U` / `VTTC0801U` |
| 잔고 | `/trading/inquire-balance` | `TTTC8434R` / `VTTC8434R` |
| 정정/취소 | `/trading/order-rvsn` · `/order-cancel` | `TTTC0803U`·`TTTC0804U` (V…) |
| 체결내역 | `/trading/inquire-daily-ccld` | `TTTC8001R` / `VTTC8001R` |

- 실전 base: `https://openapi.koreainvestment.com:9443`
- 모의 base: `https://openapivts.koreainvestment.com:29443`
- 레이트리밋: 약 8~10콜/초 (앱키 공유 시 4~5로 낮춤)

---

## 8. 부하 최적화 (검증 P1·P2 반영)

| 항목 | Before | After |
|------|--------|-------|
| 주/월봉 조회 | 매 사이클(중복 90%) | **일 1회 캐싱** |
| 한 사이클 콜 | ~274 | **~90 (67%↓)** |
| 후보 수 | 60~100 | 필요시 거래대금 상위로 제한 가능 |
| API 호출 | 순차 | 부분 병렬(ThreadPool) 검토 |

---

## 9. 테스트 계획 (테스트 우선 — 예외 없음)

각 모듈: **테스트 작성 → 구현 → pytest 통과 → 커밋** 순서 엄수.

```
tests/
 ├─ conftest.py            # 픽스처 + KIS mock 응답
 ├─ fixtures/              # 샘플 캔들·API 응답 JSON
 ├─ test_indicators.py     # EMA/ATR/RSI/MACD/OBV/VWAP
 ├─ test_structure.py      # 피봇/피보/OB/BOS·CHoCH
 ├─ test_confluence.py     # L1~L6 각각 + 합산 (핵심)
 ├─ test_gates.py          # 갭/VI/시간/한도/중복
 ├─ test_risk.py           # 사이징/호가단위/추적SL/3분할
 ├─ test_strategy.py       # 신호 생성/임계값
 ├─ test_kis_client.py     # mock 응답 파싱/주문/모의vs실전 TR
 ├─ test_trader.py         # 진입/TP감지/SL/역신호/복구
 ├─ test_scanner.py        # 깔때기/멀티종목/정렬
 └─ test_main.py           # 상태머신/재시작복구/킬스위치
```
- 순수 모듈: 라인 커버리지 100% 목표
- I/O 모듈: KIS 응답 mock으로 모든 분기 검증, 실제 주문은 **모의투자 통합테스트**로만
- 실거래 전환은 모의투자 충분 검증 후에만

---

## 10. 미해결 — KIS 모의투자로 우선 검증할 항목

코드 작성 전/중 **모의투자 환경에서 실측 확인** 필요 (추측 금지):
1. 계좌비밀번호(`ACNT_PWD`) 암호화 방식 (평문 vs 해시)
2. 호가단위를 API가 자동보정하는지 / 클라이언트가 해야 하는지
3. VI 발동 중 주문 거부 여부
4. 상한/하한가 근처 주문 거부 여부
5. `inquire-balance`와 미체결조회 TR_ID 동일 여부 (`TTTC8434R` 중복 의심)
6. 주문응답 `KRX_FWDG_ORD_ID`가 정정/취소에 필수인지
7. 공휴일/특별휴장 판별 (캘린더 수동 vs API)

---

## 11. 구현 순서 (제안)

```
1) config.py + tests          (설정·검증)
2) indicators.py + tests      (순수 지표)
3) structure.py + tests       (피봇/OB/BOS)
4) confluence.py + tests      (6레이어 — 핵심)
5) gates.py + risk.py + tests (게이트·사이징)
6) strategy.py + tests        (신호)
7) kis_client.py + tests      (API mock)
8) scanner.py + notify.py     (스캔·알림) → 여기까지 "알림 전용" 동작
9) trader.py + tests          (자동주문 — 모의투자)
10) main.py + tests           (상태머신·복구)
11) 모의투자 통합테스트 → 충분 검증 → (선택) 실거래 전환
```

8단계까지 완료하면 **기존 스캐너 + 정교화된 6레이어 알림**이 먼저 가동되고,
9단계부터 모의투자 자동주문을 붙이는 점진적·안전한 경로.
