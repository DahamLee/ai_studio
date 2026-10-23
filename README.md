# AI STUDIO 블록 계약 및 참조 실행

노코드 전략 편집기의 블록 계약, 연결 검사, 그래프 실행기와 참조 백테스트 원장을 담습니다. 표준 라이브러리만 씁니다. 실제 주문 전송, 재무 데이터, 완전한 증권 백테스트 엔진은 포함하지 않습니다. 구조와 설계 근거는 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)에 있습니다.

## 파이프라인

블록은 역할 하나만 가지고, 역할이 캔버스 단계를 정합니다. 팩터 갈래와 시그널 갈래가 같은 단계를 쓰고, 둘 다 전략 판단 → 주문 실행으로 끝납니다.

| 단계 | 역할 | 입력 → 출력 | 예 |
|---|---|---|---|
| 유니버스 | UNIVERSE | → `universe` | 구성, 제외 조건, 최소 가격, 조건 필터 |
| 지표 | INDICATOR | `universe` → `indicator` | 수익률, 이동평균, 가격, 기간 고점, 변동성, RSI (종목·시장 범위) |
| 점수·조건 | SCORE / CONDITION / LOGIC | `indicator` → `score`·`condition` | 점수화, 점수 합성, 임계·비교 조건, AND/OR/NOT |
| 선정·이벤트 | SELECTION / EVENT | → `selection`·`event` | 상위 N(보유 유지 버퍼), 상태 전이, 손절·익절, 보유기간 |
| 비중·제약·목표 | WEIGHTING / CONSTRAINT / TARGET | → `weight` → `target_portfolio` | 동일·점수·역변동성 비중, 상한, 현금, 국면 전환 |
| 판단·실행 | STRATEGY / EXECUTION | → `strategy_result` → 원장 | `strategy.state`, `strategy.event`, `execution.orders` |

- `condition`은 **지속 상태의 참·거짓**, `event`는 **그 시점에 발생한 전이**입니다. 이벤트가 없으면 청산이 아니라 `HOLD`입니다.
- `strategy.state`는 목표 포트폴리오를 받아 매 리밸런스 시점에 맞춥니다. `strategy.event`는 진입·청산 이벤트(여러 개 합침)와 선택 게이트(조건 또는 선정)를 받고, 최대 보유 수로 진입 비중과 빈 자리를 정합니다.
- 리밸런스 주기, 이탈 허용, 현금 부족 시 처리는 `execution.orders`만 정합니다.
- 자주 쓰는 조합(모멘텀 점수, 골든크로스, 시장 국면 등 8개)은 블록이 아니라 **프리셋**(저장된 하위 그래프)입니다.

## 예제

### 그래프를 만들고 1년 백테스트

```python
from ai_studio import RunSettings, history_from_bars, load_ohlc, run_graph_backtest
from ai_studio.n12_strategies import EXAMPLES

history = history_from_bars(load_ohlc())
name, build = EXAMPLES["momentum-gated-cross"]   # 모멘텀 상위 30 안에서 골든크로스 진입
book = run_graph_backtest(build(), history, RunSettings(
    signal_frequency="daily", initial_capital=100_000, fee_rate=0.001, slippage_rate=0.0005,
))
print(name, len(book.trade_log), f"{book.portfolio_history[-1].cumulative_return:+.2%}")
```

`run_graph_backtest`는 거래일마다 현재 보유를 넣어 그래프를 실행하고, 신호는 그날 종가, 체결은 다음 거래일 시가, 평가는 그날 종가로 남깁니다. 결과는 `signal_history`, `trade_log`, `position_history`, `portfolio_history`, `skipped_orders`(체결 못 한 주문)입니다.

### 프리셋으로 직접 조립

```python
from ai_studio import StrategyEdge, StrategyGraph, StrategyNode, validate_strategy
from ai_studio.n12_strategies import place

cross = place("golden-cross", "cross", {"universe": ("universe", "universe")},
              {"fast": {"windowDays": 10}, "slow": {"windowDays": 40}})
graph = StrategyGraph(
    (StrategyNode("universe", "universe.compose", {}),) + cross.nodes + (
        StrategyNode("stop", "event.stop", {"stopLossPct": 8}),
        StrategyNode("decision", "strategy.event", {"maxPositions": 5}),
        StrategyNode("orders", "execution.orders", {}),
    ),
    cross.edges + (
        StrategyEdge("universe", "universe", "stop", "universe"),
        StrategyEdge("universe", "universe", "decision", "universe"),
        StrategyEdge(*cross.out("entry"), "decision", "entry"),
        StrategyEdge(*cross.out("exit"), "decision", "exit"),
        StrategyEdge("stop", "exit", "decision", "exit"),
        StrategyEdge("decision", "result", "orders", "result"),
    ),
)
print(validate_strategy(graph))   # ConnectResult(ok=True, reason=None, warnings=())
```

### 한 시점만 실행

```python
from ai_studio import ExecutionContext, execute, momentum_top10

as_of = max(bar.timestamp for rows in history.values() for bar in rows)
result = execute(momentum_top10(), ExecutionContext(as_of, history))
print(result.port("target", "portfolio"))
print(result.warnings)   # 데이터가 모자라 빈 지표·점수가 있으면 필요한 봉 수와 함께 나온다
```

## 실행

```bash
python -m ai_studio.n16_self_check            # 카탈로그·레지스트리·프리셋·예제 정합성
python -m unittest discover -s tests -v       # 저장된 일봉이 있으면 예제 8개 백테스트 포함
PYTHONPATH=. .venv/bin/python scripts/run_momentum_backtest.py
PYTHONPATH=. .venv/bin/python scripts/preview_blocks.py

cd web && npm install && npm run dev          # 편집기. /api/validate·/api/backtest가 Python을 부른다
```

## 백테스트 적용 전 점검

이 참조 구현은 시점별 입력을 호출자가 제공합니다. 재무자료의 실제 공개시점, 지수 편입 이력, 상장폐지, 휴장일, 분할·배당, 세금, 부분체결과 실제 주문 거부를 처리하지 않습니다. 시장 범위 지표는 적재된 종목의 동일가중 합성 지수입니다. 실데이터 어댑터는 `available_at <= 신호 계산 시각`을 보장해야 하고, 수정주가로 만든 신호와 실제 체결가격·수량은 기업행동 처리로 맞춰야 합니다. 신호를 만든 당일 종가 체결은 허용하지 않습니다. 다음 시가가 올라 현금이 모자라면 실행 노드의 `shortfall` 정책에 따라 수량을 줄이거나(기본) 오류를 냅니다.
