# AI STUDIO 블록 계약 및 참조 실행

노코드 전략 편집기의 블록 계약, 연결 검사, 그래프 실행기와 참조 백테스트 원장을 담습니다. 표준 라이브러리만 씁니다. 실제 주문 전송, 재무 데이터, 완전한 증권 백테스트 엔진은 포함하지 않습니다. 구조와 설계 근거는 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)에 있습니다.

## 파이프라인

블록은 역할 하나만 가지고, 역할이 캔버스 단계를 정합니다. 팩터 갈래와 시그널 갈래가 같은 단계를 쓰고, 둘 다 판단 → 주문으로 끝납니다.

| 단계 | 역할 | 입력 → 출력 | 예 |
|---|---|---|---|
| 1 종목 고르기 | UNIVERSE | → `universe` | 종목 불러오기, 투자 제외하기, 저가주 빼기 |
| 2 지표 계산하기 | INDICATOR | `universe` → `indicator` | 수익률·이동평균·가격·기간 고점·변동성·RSI 구하기 (종목·시장 범위) |
| 3 평가하기 | SCORE / CONDITION / LOGIC | `indicator` → `score`·`condition` | 점수 매기기·합치기, 기준값·두 지표 비교하기, 조건 묶기·뒤집기 |
| 4 매매 대상 정하기 \| 매매 신호 잡기 | SELECTION / EVENT | → `selection`·`event` | 대상: 상위 N개 고르기(대상 조건, 보유 유지 버퍼), 점수 기준으로 고르기, 조건 맞는 종목 고르기 · 신호: 진입·청산 시점 잡기, 손절·익절 시점 잡기, 보유기간 만료 시점 잡기 |
| 5 비중 정하기 | WEIGHTING / CONSTRAINT / TARGET | → `weight` → `target_portfolio`, → `event_plan` | 비중 나누기, 최대 보유 수로 비중 정하기, 비중 상한 두기, 현금 남기기, 국면 따라 줄이기 |
| 6 매매 결정하기 | STRATEGY | → `strategy_result` | 리밸런싱 하기, 신호와 보유 비교하기 (현재 보유와 비교해 살 것·팔 것·둘 것을 정한다) |
| 7 주문하기 | EXECUTION | `strategy_result` → 원장 | 주기·체결 방식 정하기 (주식 수 계산, 리밸런스 주기, 현금 부족 처리) |

- **선은 순서대로만 이어집니다.** 주 입력은 같은 단계나 바로 앞 단계에서만 받고, 뒤 단계에서 앞 단계로는 이을 수 없습니다. 보조 입력(국면 따라 줄이기의 투자 조건, 지표로 비중 나누기의 지표, 최대 보유 수로 비중 정하기의 게이트)만 단계를 건너뜁니다. 판단 노드는 유니버스를 직접 받지 않습니다.
- 4단계는 한 띠 안에 두 칸입니다. 왼쪽 **매매 대상 정하기**는 "주기마다 어떤 종목을 들고 있을까"(상태형), 오른쪽 **매매 신호 잡기**는 "무슨 일이 생긴 순간 사고팔까"(이벤트형)입니다. 갈래는 지표 종류(기술적·재무)가 아니라 전략 방식으로 정해집니다.
- `condition`은 **지속 상태의 참·거짓**, `event`는 **그 시점에 발생한 전이**입니다. 이벤트가 없으면 청산이 아니라 `HOLD`입니다.
- 팩터 갈래는 목표 포트폴리오 → `strategy.state`(리밸런싱 하기), 시그널 갈래는 진입·청산 신호 → `allocation.event-slots`(최대 보유 수로 비중 정하기: 최대 보유 수, 게이트) → `strategy.event`(신호와 보유 비교하기)로 갑니다.
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
        StrategyNode("stop", "event.stop", {"stopLossPct": 8}),            # 보유 종목만 보므로 입력이 없다
        StrategyNode("slots", "allocation.event-slots", {"maxPositions": 5}),
        StrategyNode("decision", "strategy.event", {}),
        StrategyNode("orders", "execution.orders", {}),
    ),
    cross.edges + (
        StrategyEdge(*cross.out("entry"), "slots", "entry"),
        StrategyEdge(*cross.out("exit"), "slots", "exit"),
        StrategyEdge("stop", "exit", "slots", "exit"),
        StrategyEdge("slots", "plan", "decision", "plan"),
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

## 데이터

| 파일 | 내용 | 만드는 법 |
|---|---|---|
| `data/sp500_ohlc_1y.csv` | S&P 500 503종목 일봉 (date, symbol, open, high, low, close, volume) | `scripts/fetch_yahoo_ohlc.py` |
| `data/sp500_fundamentals_sample.csv` | 같은 503종목의 분기 재무 **예시값**(가짜), 2024Q3~2026Q2, 계정 11개 | `scripts/make_sample_fundamentals.py` |

재무 표는 한 행이 "종목 × 계정 × 분기"인 long 형식입니다.

| symbol | account_code | value | unit | fiscal_period | fiscal_date | upload_date |
|---|---|---|---|---|---|---|
| AAPL | ROE | 25.85 | % | 2025Q3 | 2025-09-30 | 2025-11-14 |

- `fiscal_date`는 공시가 적용되는 회계 기준일(분기 말), `upload_date`는 공시가 실제로 올라온 날입니다. 2025Q3 실적은 2025-11-14부터 쓸 수 있습니다.
- 계정코드: REVENUE, NET_INCOME, TOTAL_ASSETS, TOTAL_LIABILITIES, TOTAL_EQUITY, SHARES_OUTSTANDING(백만 단위), EPS, BPS(달러), ROE, ROA, DEBT_RATIO(퍼센트 값, 10%는 10.0). ROE·ROA는 최근 4분기 순이익 기준입니다.
- 일봉과는 **`upload_date` 기준 as-of 조인**으로 붙입니다. `fiscal_date`로 붙이면 공개 전 실적을 미리 보는 미래 참조가 됩니다.

```python
fund = pd.read_csv("data/sp500_fundamentals_sample.csv", parse_dates=["fiscal_date", "upload_date"])
wide = fund.pivot_table(index=["symbol", "fiscal_period", "fiscal_date", "upload_date"],
                        columns="account_code", values="value").reset_index()
ohlc = pd.read_csv("data/sp500_ohlc_1y.csv", parse_dates=["date"])
joined = pd.merge_asof(ohlc.sort_values("date"), wide.sort_values("upload_date"),
                       left_on="date", right_on="upload_date", by="symbol", direction="backward")
```

## 백테스트 적용 전 점검

이 참조 구현은 시점별 입력을 호출자가 제공합니다. 재무자료의 실제 공개시점, 지수 편입 이력, 상장폐지, 휴장일, 분할·배당, 세금, 부분체결과 실제 주문 거부를 처리하지 않습니다. 시장 범위 지표는 적재된 종목의 동일가중 합성 지수입니다. 실데이터 어댑터는 `available_at <= 신호 계산 시각`을 보장해야 하고, 수정주가로 만든 신호와 실제 체결가격·수량은 기업행동 처리로 맞춰야 합니다. 신호를 만든 당일 종가 체결은 허용하지 않습니다. 다음 시가가 올라 현금이 모자라면 실행 노드의 `shortfall` 정책에 따라 수량을 줄이거나(기본) 오류를 냅니다.
