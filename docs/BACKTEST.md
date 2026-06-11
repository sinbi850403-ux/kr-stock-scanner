# 백테스트 모듈 — 로컬 검증 가이드

> **로컬 환경에서만 실행 가능.** `pykrx` 라이브러리 필요 (requirements-dev.txt).

---

## 0. 설치 & 기본 사용

### 의존성 설치
```bash
pip install -r requirements-dev.txt
```

### CLI 실행
```bash
# 단일 종목
python backtest.py 005930

# 여러 종목
python backtest.py 005930 000660 035720

# 파일에서 로드
echo "005930\n000660\n035720" > symbols.txt
python backtest.py --file symbols.txt
```

---

## 1. 백테스트 로직 — 핵심 가정

### 데이터 소스
- **pykrx 일봉 3년**: 지연 임포트 (메인 봇 무영향).
- **주/월봉**: pandas `resample("W"/"ME")` 로 생성.

### 진입 조건
```
신호 발생: score >= threshold AND ext_pct <= max_ext_pct
→ 다음날 시가 진입 (order fill at next bar's open)
```

### 손절·익절
```
SL = entry - ATR × sl_atr_mult
TP1 = entry + (entry - SL) × tp1_r
TP2 = entry + (entry - SL) × tp2_r
TP3 = entry + (entry - SL) × tp3_r
```

### 추적 손절 (Breakeven)
```
TP1 체결 후 → SL을 entry + 0.5R로 상향이동
```

### 역신호 청산
```
EMA 역배열 (e5 < e20 < e60) 발생 시
→ 그날 종가로 강제 청산
```

### 같은 캔들 SL/TP 충돌
```
보수적 원칙: SL을 먼저 우선 처리
```

### 거래비용
```
매도 시 차감:
  - 세금: 0.18%
  - 수수료: 0.03%
  - 합계: 0.21%
```

---

## 2. 출력 파일

### CSV 거래 기록 (`backtest_trades.csv`)
```
symbol,entry_date,entry_price,exit_date,exit_price,qty,pnl,pnl_r,reason
005930,20240115,70200,20240118,71000,100,165000,1.65,TP
005930,20240125,69500,20240130,68900,-80000,-0.80,SL
...
```

### 콘솔 요약
```
=== 백테스트 결과 ===
총 거래: 42
승률: 61.9%
평균 R: 0.87R
최대 손실: -165,000원
```

---

## 3. 중요 가정 & 제약

| 항목 | 가정 | 이유 |
|------|------|------|
| **진입 시각** | 다음날 시가 | 보수적 (신호 생성 후 1세션 지연) |
| **룩어헤드** | t까지의 데이터만 사용 | 미래 정보 유출 방지 |
| **갭 다운** | 진입가가 SL 이상 필요 | SL 위치에서 즉시 청산 방지 |
| **수수료** | 왕복 0.21% | KR 실전 기준 |
| **슬리피지** | 무시 | 각 주문이 시가/종가 정확히 체결 가정 |
| **자금** | 고정 100,000원 리스크 | 위치 크기 자동 계산 |
| **제약** | 도시락 없음, 갭 흡수 없음 | 데이터 기반 순수 알고리즘 검증 |

---

## 4. 해석 예시

### 좋은 결과
```
총 거래: 30
승률: 70%
평균 R: 1.2R
→ 2020 거래당 가치 있음 (승률 > 50% AND 평균 R > 0)
```

### 주의 신호
```
총 거래: 100+
승률: 45%
평균 R: -0.5R
→ 엣지 부족 (실전 적합성 낮음)
```

---

## 5. 테스트 케이스

### 단위 테스트 (`tests/test_backtest.py`)
```bash
python -m pytest tests/test_backtest.py -v
```

- 합성 데이터로 진입/청산 로직 검증
- 룩어헤드 없음 확인
- 비용 차감 검증
- 상태 저장·복원 라운드트립

### 커버리지
```
$ pytest --cov=backtest tests/test_backtest.py
Name           Stmts   Miss  Cover
backtest.py      150     10    93%
```

---

## 6. FAQ

**Q: 왜 거래가 0개인가?**  
A: 신호 조건이 까다로움 (confluence 6레이어 + 상위TF 확인).  
더 많은 거래 원하면 `threshold` 낮추기 또는 `max_ext_pct` 확대.

**Q: 실전 결과와 다를까?**  
A: 네, 예상되는 차이:
  - 갭 다운 (SL 아래로 갭): 백테스트 무시 → 실전에서 손실 증가
  - 주문 체결 실패: 백테스트 100% 가정 → 실전 ~99%
  - 심리적 오버트레이딩: 백테스트 무시 → 실전 규율 감소

**Q: 슬리피지/커미션을 더 현실적으로?**  
A: `COST_RATE` 상수 또는 CLI 파라미터로 조정 가능.

---

## 참고

- **SOURCE OF TRUTH**: `confluence.py`, `strategy.py` (신호 생성)
- **시뮬레이터**: `backtest.py` (백테스트 엔진)
- **테스트**: `tests/test_backtest.py`
