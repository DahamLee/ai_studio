# AI STUDIO 블록 계약 및 참조 실행

이 저장소는 노코드 전략 편집기의 블록 메타데이터, 연결 검사, 작은 순수 함수 기반 실행 예제를 담습니다. 시세·재무 데이터 조회, 실제 주문 전송 및 완전한 증권 백테스트 엔진은 포함하지 않습니다.

## 파이프라인과 공통 계약

| 순서 | 입력 → 출력 | 역할 |
|---|---|---|
| 유니버스 | 설정 → `universe` (`UniverseFrame`) | 시각·종목 집합 고정 |
| 지표·상태 조건 | `universe` + 시점별 데이터 → `score` 또는 `signal` | 점수와 지속 상태 계산 |
| 이벤트 탐지 | `universe` + 시계열 → `event` | 전일·당일 교차 등 발생 시점만 출력 |
| 전략 판단 | `universe` + `signal`/`event` → `strategy_result` (`StrategyResult`) | 모든 전략의 동일 출력 계약 |
| 비중·주문 | 결과 + 보유·현금·기준가격 → `OrderIntent` | 목표 비중, 온주 수량, 현재수량 차이 |
| 체결·기록 | 주문 + 다음 거래 가능 시가 → `Fill`, `PositionState` | 현금·평균진입가·실현손익 반영 |

`signal`은 **지속 상태의 참·거짓**, `event`는 **그 시점에 발생한 전이**입니다. 이벤트의 `False`는 청산이 아니라 `HOLD`입니다. `strategy.state`, `strategy.event`, `strategy.hybrid`의 첫 입력은 유니버스 출력과 같은 `universe`이고, 출력은 모두 `strategy_result`입니다. 결과 envelope에는 시각, 전략 ID, 실행유형, 유니버스, 종목별 `AssetSignal`, 목표 비중, 사유 및 진단을 담습니다. 미사용 필드는 `None` 또는 빈 튜플로 둡니다.

| 실행유형 | 판단 | 미발생·미충족 시 | 주문 시점 |
|---|---|---|---|
| `STATE_REBALANCE` | 현재 상태에서 목표 보유·비중 산출 | 목표 비중 0 | 지정 리밸런싱일/정책 충족 시 |
| `EVENT_LIFECYCLE` | `ENTER`/`EXIT` 순간과 현재 보유 비교 | 수량 유지 | 새 이벤트 발생 시 |
| `HYBRID` | 상태 조건이 진입을 허용하고 이벤트가 전이 결정 | 수량 유지 | 유효한 이벤트 발생 시 |

`MULTI_LEG_EVENT`, `EXECUTION_ALGORITHM`, `ANALYSIS_FUNCTION`은 화면용 중앙 메타데이터에 등록했으나 **현재의 단일 종목 참조 실행기에서 수행하지 않습니다**. 그룹 단위 부분체결·집행 스케줄·분석 결과는 별도 계약과 엔진이 필요합니다. 지원하지 않는 유형을 `StrategyResult`로 실행하면 오류가 납니다.

## 기존 코드와 변경 범위

기존 14개 블록 ID, 포트 키, `get_function`, `can_connect`, `validate_graph`는 유지합니다. `momentum.factor`와 팩터 블록의 `score`는 중간 점수이며 최종 전략 결과가 아닙니다. 기존 `momentum.trend`의 `signal`은 상태 조건으로 정의하고, 새로운 `momentum.ma-cross`의 `event`를 교차 순간에 사용합니다. 기존 `allocation.target-weight`의 여러 허용 타입은 기존 화면 호환용으로 유지했으나, 신규 편집기에서는 점수→종목 선정, 조건→종목 선정의 변환 규칙을 명시한 뒤 비중 블록에 연결해야 합니다. `RUN_CONTEXT`의 리밸런싱 주기와 `allocation.rebalance`의 주기가 중복될 수 있으므로 UI에서는 정책 소유자를 하나로 정해야 합니다.

블록에는 `version`, 입력·출력 스키마 버전, 역할, 실행유형, `RequiredData(fields, minimum_history, frequency)`가 추가됐습니다. 데이터 요구 기간은 설정 기간에 따라 늘어날 수 있으므로 실행 시 동적 검증이 필요합니다. `validate_graph`는 필수 포트·타입·중복·순환·파라미터 범위를 검사합니다.

## 단계별 예제

### 상태 평가·리밸런싱

```python
from datetime import datetime, timezone
from ai_studio.runtime import UniverseFrame, evaluate_state, generate_orders

t = datetime(2026, 9, 29, 20, tzinfo=timezone.utc)
u = UniverseFrame(t, ("AAPL", "MSFT"))
result = evaluate_state("trend", u, {"AAPL": True, "MSFT": False})
# signals: AAPL REBALANCE/LONG/1.0, MSFT REBALANCE/FLAT/0.0
orders = generate_orders(result, {}, {"AAPL": 100, "MSFT": 200},
                         portfolio_value=1000, cash=1000, rebalance_due=True)
# orders: AAPL BUY 10주. rebalance_due=False이면 주문 없음.
```

보유 중인 종목이 유니버스에서 제거됐다면 `evaluate_state(..., positions=...)`로 넘깁니다. 청산 목표 0이 결과에 포함됩니다.

### 골든크로스 이벤트

```python
from ai_studio.runtime import moving_average_cross_events, evaluate_events, fill_at_next_open

# MarketBar는 종가 시각(timestamp), 실제 공개 시각(available_at),
# 다음 봉의 시가 시각(open_at)을 구분합니다.
crosses = moving_average_cross_events(history, fast_days=20, slow_days=60)
# 이전 단기평균 <= 이전 장기평균, 현재 단기평균 > 현재 장기평균인 날만 entry=True
result = evaluate_events("golden_cross", universe, {"AAPL"}, set(), positions)
# FLAT: ENTER/LONG; 이미 LONG: HOLD/UNCHANGED
orders = generate_orders(result, positions, reference_prices,
                         portfolio_value=1000, cash=1000)
fills, new_positions, cash = fill_at_next_open(orders, next_bars, positions, 1000)
# 주문은 신호 이후의 명시된 다음 시가 시각에만 체결됩니다.
```

`run_backtest`는 미리 계산한 `StrategyResult`와 시점별 기준가격·다음 거래 봉을 받아 `signal_history`, `trade_log`, `position_history`, `portfolio_history`를 별도로 반환합니다. 신호가 있었으나 현금이 없어 주문하지 못한 경우도 신호 이력에 남습니다. 이벤트 ID와 미체결 주문을 제공하면 반복 주문을 억제합니다.

## 백테스트 적용 전 점검

이 참조 구현은 시점별 입력을 **호출자가 제공**합니다. 재무자료의 실제 공개시점, 지수 편입 이력, 상장폐지, 거래정지, 분할·배당, 세금, 거래소 캘린더, 부분체결 및 실제 주문 거부를 자동 처리하지 않습니다. 실데이터 어댑터는 `available_at <= 신호 계산 시각`을 보장해야 하며, 수정주가로 만든 신호와 실제 체결가격·수량은 기업행동 처리로 맞춰야 합니다. 기준 종가로 신호를 만든 당일 종가 체결은 허용하지 않습니다. 다음 시가가 예상 가격보다 급변해 현금이 부족하면 참조 체결기는 명확한 오류를 반환합니다. 운영 백테스트에서는 주문 축소·거부 정책과 미체결 상태를 추가해야 합니다.

```bash
python -m ai_studio.self_check
python -m unittest discover -s tests -v
```
