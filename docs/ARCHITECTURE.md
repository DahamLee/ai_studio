# AI STUDIO 코드 구조 명세

> 기준: `main` 작업 트리 (2026-10-23). 테스트 45개 통과, `python -m ai_studio.n16_self_check` 통과.

## 1. 개요

노코드 전략 편집기의 **블록 계약(메타데이터) + 연결 검사 + 그래프 실행기 + 참조 백테스트 원장**을 순수 Python(표준 라이브러리만)으로 구현한 저장소다. 웹(`web/`)은 블록 팔레트와 캔버스를 그리는 React 프로토타입이다.

```
ai-studio/
├── ai_studio/          # 핵심 패키지 (n01 ~ n16, 약 3,500줄)
├── tests/              # unittest 45개
├── scripts/            # 데이터 수집, 실행 예제
├── notebooks/          # 단계별 설명 노트북 2개
├── data/sp500_ohlc_1y.csv   # S&P 500 503종목 × 251거래일 일봉 (약 12MB)
└── web/                # Vite + React 19 + TS 편집기
```

## 2. 전체 흐름

```
[편집기 web/]  ──catalog.json──  n10_functions.function_catalog()
      │
      ▼  StrategyGraph(nodes, edges)
n13_connect.validate_graph   ← 포트 타입·필수·중복·순환·파라미터 검사
      │
      ▼
n14_engine.execute(graph, ExecutionContext)
      │   위상 정렬 → 노드마다 n11_registry 핸들러 호출
      ▼
블록 계산 (n08_blocks → n04 지표 / n05 신호 / n06 포트폴리오)
      │   UniverseFrame → Score/Condition/Event → Selection → Weight → TargetPortfolio
      │                                                   └→ StrategyResult (strategy.* 블록)
      ▼
n07_runtime.generate_orders → fill_at_next_open      (주문 의도·다음 시가 체결)
      ▼
n09_backtest.run_backtest → BacktestResult           (신호·체결·포지션·포트폴리오 이력)
```

`n15_ohlc_backtest.run_momentum_topn`이 이 전체를 CSV 데이터에 연결한 실행 예다.

## 3. 모듈별 명세

### 3.1 계층과 의존 관계

| 계층 | 모듈 | 의존 |
|---|---|---|
| 데이터 타입 | `n01_frames`, `n02_schema` | 없음 |
| 데이터 소스 | `n03_market_data` | 없음 |
| 순수 계산 | `n04_indicators`, `n05_signals`, `n06_portfolio` | n01, n04, **n07** |
| 런타임·원장 | `n07_runtime`, `n09_backtest` | n02, n07 |
| 블록 구현 | `n08_blocks` | n01, n03–n07 |
| 카탈로그 | `n10_functions` | n02 |
| 핸들러 등록 | `n11_registry` | n01, n05–n08, n10 |
| 그래프 | `n12_strategies`, `n13_connect`, `n14_engine` | n02, n10, n11, n13 |
| 애플리케이션 | `n15_ohlc_backtest`, `n16_self_check` | 위 전부 |

### 3.2 `n01_frames` — 블록 사이의 데이터 프레임

모두 `frozen=True, slots=True` 데이터클래스이며 생성 시 `instrument_id` 비어 있음·중복을 검사한다.

| 프레임 | 행 | 불변식 |
|---|---|---|
| `UniverseFrame(as_of: str, rows)` | `UniverseRow(instrument_id, eligible, metadata)` | `as_of` 필수. `symbols()`는 eligible만 |
| `ScoreFrame` | `ScoreRow(id, score, raw_value, rank, eligible, metadata)` | 높을수록 선호 |
| `IndicatorFrame` | `IndicatorRow(id, value)` | 좋고 나쁨 없음 |
| `ConditionFrame` | `ConditionRow(id, matched)` | 거짓도 행으로 남김 |
| `EventFrame` | `EventRow(id, as_of: datetime, kind: entry/exit)` | 발생한 것만. tz 필수, (id, 시각, kind) 고유 |
| `SelectionFrame` | `SelectionRow(id, rank, score)` | rank 1부터 고유 |
| `WeightFrame` | `WeightRow(id, weight)` | 음수 금지, 합 ≤ 1 |
| `TargetPortfolio(positions, cash_weight)` | `TargetPosition(id, target_weight)` | 합 + 현금 = 1 |

### 3.3 `n02_schema` — 블록 계약 타입

- `PortType`: `universe, condition, event, indicator, score, selection, weight, target_portfolio, strategy_result`
- `ExecutionType`: `STATE_REBALANCE, EVENT_LIFECYCLE, HYBRID` (실행 가능) / `MULTI_LEG_EVENT, EXECUTION_ALGORITHM, ANALYSIS_FUNCTION` (메타데이터만)
- `PortSpec(key, label, type, description, required, accepts, multiple, min_connections, same_type_as, mirrors)`
  - `accepts`: 다형 포트 허용 타입, `same_type_as`: 형제 포트와 타입 일치, `mirrors`: 출력 타입이 지정 입력을 따름
- `ParamSpec(key, label, type, description, default, options, unit, min, max)` — `type ∈ number, integer, enum, boolean, date`
- `FunctionSpec(id, name, category, ..., inputs, outputs, params, version, role, strategy_execution_type, required_data, *_schema_version)`
- `RequiredData(fields, minimum_history, frequency)`
- `StrategyNode(id, function_id, params)`, `StrategyEdge(from_node, from_port, to_node, to_port)`, `StrategyGraph(nodes, edges)`
- `RUN_CONTEXT`: 화면 하단 공통 실행 조건 9개 (기간, 룩백, 신호·리밸런스 주기, 초기자금, 통화, 수수료, 슬리피지)

### 3.4 `n03_market_data`

`DailyBar(date: str, symbol, open, high, low, close, volume)`. `load_ohlc(path)`가 호출할 때마다 CSV 전체를 읽는다.

### 3.5 `n04_indicators` / `n05_signals` / `n06_portfolio` — 순수 계산

| 모듈 | 함수 |
|---|---|
| n04 | `moving_average`, `window_high`, `rsi`(단순평균 방식, Wilder 평활 아님) |
| n05 | `compare_threshold(IndicatorFrame, op, x) → ConditionFrame`, `crossed_above/below`, `breaks_prior_high`, `moving_average_cross_events(MarketBar[]) → EventFrame` (중복·미래 공개 데이터 검사 포함) |
| n06 | `select_top_n`, `select_min_score`, `weight_equal`, `weight_by_score`, `cap_max_weight`(남는 비중 재분배 안 함), `apply_cash_weight`(합을 1−현금으로 스케일), `to_target_portfolio` |

### 3.6 `n07_runtime` — 전략 결과·주문·체결

- 자체 `UniverseFrame(as_of: datetime, symbols)`, `MarketBar(timestamp, available_at, symbol, open, close, frequency, open_at)`
- `AssetSignal(symbol, eligible, score, action ∈ ENTER/EXIT/HOLD/REBALANCE/NONE, desired_state ∈ LONG/FLAT/SHORT/UNCHANGED, target_weight, reason_codes, event_id)`
- `StrategyResult` — 모든 전략 블록의 공통 출력. 유니버스 밖 종목은 STATE_REBALANCE의 FLAT/0만 허용, 비중 합 ≤ 1
- `PositionState`, `OrderIntent`, `Fill`
- `evaluate_state`: 참이면 동일비중 LONG, 거짓이면 0. 유니버스에서 빠진 보유종목은 `REMOVED_FROM_UNIVERSE`로 청산
- `evaluate_events`: EXIT 우선, 이미 보유 중 ENTER는 HOLD. `eligible`이 있으면 HYBRID
- `generate_orders`: 종가 기준 목표수량 `floor(평가금액×비중/가격)`, 매도 먼저·매수는 남은 현금 한도. 미체결·처리된 이벤트 ID로 중복 주문 억제
- `fill_at_next_open`: `open_at > signal_at`인 봉의 시가 ± 슬리피지로 체결, 수수료 별도. 현금 부족·초과 매도 시 예외

### 3.7 `n08_blocks` — 화면 블록 계산

입력은 `history: {symbol: DailyBar[]}`, `symbols`, `as_of: str`. 한 달 = 21거래일.

`compose_universe`, `exclude_universe`(상장일수·평균거래대금·거래정지), `min_price_universe`, `momentum_score`(skip 개월 제외 수익률), `trend_condition`(가격>MA 또는 MA 정배열), `breakout_condition`, `low_volatility_score`(표준편차/동일가중 시장 대비 베타, 점수 = −값), `rsi_indicator`, `moving_average_cross`, `combine_conditions`, `if_else_score/condition`, `set_operation`, `set_condition_operation`, `allocate_target_weight`, `apply_stop`, `rebalance_weights`

### 3.8 `n09_backtest` — 참조 원장

`run_backtest(results, reference_prices, next_bars, initial_cash, fee_rate, slippage_rate, rebalance_dates)` → `BacktestResult(signal_history, trade_log, position_history, portfolio_history)`. 평가는 다음 봉 종가로만 한다. `trade_rows` / `write_trades_csv`로 체결 CSV 출력.

### 3.9 `n10_functions` — 블록 카탈로그 (26개)

| 역할 | 블록 ID |
|---|---|
| UNIVERSE | `universe.compose`, `universe.exclude`, `universe.min-price` |
| SCORE | `momentum.factor`, `factor.value`*, `factor.quality`*, `factor.low-volatility` |
| CONDITION | `momentum.trend`, `momentum.breakout`, `condition.threshold` |
| INDICATOR | `indicator.rsi` |
| EVENT | `momentum.ma-cross` (entry, exit 출력) |
| LOGIC | `logic.combine`, `logic.if-else`, `logic.set-op` |
| SELECTION | `selection.top-n`, `selection.threshold` |
| WEIGHTING | `allocation.target-weight` |
| CONSTRAINT | `allocation.stop`, `allocation.rebalance`, `portfolio.max-weight`, `portfolio.cash` |
| TARGET | `portfolio.target` |
| STRATEGY | `strategy.state`, `strategy.event`, `strategy.hybrid` → `strategy_result` |

\* 재무 데이터가 없어 실행 시 예외를 낸다.

`_LEGACY_FUNCTIONS`(기존 17개)는 `replace()`로 `role`·`required_data`를 덧입히고, `_NEW_FUNCTIONS`(9개)는 직접 정의한다. `function_catalog()`가 웹용 JSON을 만든다.

### 3.10 `n11_registry` — 블록 ID → 핸들러

`Handler(inputs, params, context) → {output_port: value}`. 모듈 import 시 `register_builtin_handlers()`로 26개를 등록한다. 핸들러가 `n01.UniverseFrame(str)` ↔ `n07.UniverseFrame(datetime)` 변환과 이벤트 날짜 필터(`as_of`와 같은 날의 이벤트만)를 담당한다.

### 3.11 `n12_strategies` / `n13_connect` / `n14_engine`

- `n12`: 미리 만든 그래프 `momentum_rebalance(...)`, `momentum_top10()`
- `n13`: `can_connect`, `output_type`, `validate_graph`(노드 중복 → 순환 → 파라미터 → 엣지 타입(mirrors 해석) → 필수·개수·same_type_as), `validate_params`(타입·범위 + `fastDays<slowDays`, `skipMonths<lookbackMonths`)
- `n14`: `ExecutionContext(as_of, history, entry_prices, peak_prices, actual_weights, rebalance_due, positions)`, `execute`(검증 → 노드 ID 사전순 Kahn 위상정렬 → 핸들러 호출 → 출력 포트 집합 검사), `ExecutionResult.port(node, port)`

### 3.12 `n15_ohlc_backtest` / `n16_self_check`

- `run_momentum_topn(lookback=20, top_n=10, step=21, cash_weight=0.2)`: 종목 수 450개 이상인 날만 거래일로 보고, `step`일마다 그래프를 실행해 `StrategyResult`를 직접 만든 뒤 `run_backtest`에 넘긴다. (1년 데이터 기준 0.4초, 체결 201건)
- `n16`: 블록 수 26개, 기본 연결 규칙 스모크 테스트

### 3.13 웹 (`web/`)

- `npm run dev` → Python으로 `catalog.json` 생성 후 Vite 실행
- `layers.ts`: 캔버스 3층(유니버스 / 조건 / 매수·매도 시그널)에 블록 ID를 하드코딩
- `App.tsx`: 팔레트 검색, 드래그 배치, `connectAll`이 **앞에 놓인 블록 중 `port.type`이 같은 출력에 자동 연결**, 선택 노드의 인스턴스 JSON 표시. 그래프 내보내기·서버 검증·실행 호출은 없다.

## 4. 핵심 설계 원칙 (코드에서 확인되는 것)

1. **단계 분리**: 점수 ≠ 선정 ≠ 비중 ≠ 제약 ≠ 목표. 각 블록은 한 가지 변환만 한다.
2. **조건 vs 이벤트**: 조건은 모든 종목에 참·거짓, 이벤트는 발생 시점만. 이벤트 미발생은 HOLD이며 청산이 아니다.
3. **look-ahead 방지**: `available_at`, `open_at > signal_at`, 다음 시가 체결, 평가는 다음 봉 종가.
4. **불변·검증 우선**: 모든 값 객체가 frozen이고 `__post_init__`에서 불변식을 검사한다.
5. **확장 지점**: `register(function_id, handler)`로 핸들러를 추가하고 실행기는 손대지 않는다.

---

## 5. 개선점

우선순위: 🔴 동작 오류·오해 소지 / 🟡 설계·유지보수 / 🟢 정리

### 🔴 동작·계약 문제

| # | 위치 | 문제 | 제안 |
|---|---|---|---|
| 1 | `n10` `momentum.factor` 기본값, `_REQUIRED_DATA` | 기본값(12개월 + 1개월 제외)이면 `13×21+1 = 274`일이 필요한데 `RequiredData.minimum_history = 252`로 적혀 있다. 동봉 데이터는 251일이라 **기본 블록이 점수를 0행 반환한다** (직접 확인). 경고 없이 빈 결과가 이어진다. | `minimum_history`를 파라미터로 계산하는 함수로 바꾸고, `execute` 전에 데이터 길이를 검사한다. 점수가 0행이면 진단 메시지를 남긴다. |
| 2 | `n10` `logic.if-else` ↔ `n11._if_else` | 스펙은 `universe, condition, score, weight`를 받는다고 하지만 핸들러는 score/condition만 처리한다. 편집기에서 연결이 허용된 그래프가 실행 시 실패한다. | `accepts`를 `("condition", "score")`로 줄이거나 universe·weight 분기를 구현한다. 스펙의 `accepts`와 핸들러가 받는 타입을 대조하는 테스트를 추가한다. |
| 3 | `n10` `allocation.rebalance` | `cadence` 파라미터를 핸들러가 읽지 않는다. 실제 주기는 `context.rebalance_due`가 정하고, `RUN_CONTEXT.rebalanceFrequency`와도 겹친다 (README도 언급). | 주기 정책을 한 곳(`RUN_CONTEXT` 권장)에만 두고 블록에서는 `cadence`를 빼거나, 핸들러가 `cadence`로 `due`를 계산한다. |
| 4 | `n11._events` | `entry_weight = 1 / 오늘 진입 수`라서 기존 보유를 고려하지 않는다. 보유 5종목에 오늘 3종목이 새로 진입하면 각각 1/3을 목표로 잡아 합이 1을 넘고, 결국 `generate_orders`의 현금 한도에서 조용히 잘린다. | 진입 비중 정책(고정 비율, 최대 보유 수 N에 대한 1/N 등)을 파라미터로 노출한다. |
| 5 | `n15.run_momentum_topn` | `strategy.state` 블록을 쓰지 않고 `StrategyResult`를 손으로 만든다. 그래프 실행 경로와 백테스트 경로가 따로 있다. `strategy_id="momentum-top10"`이 `top_n`과 상관없이 고정되고, `fee_rate`·`slippage_rate`를 넘기지 않으며, `450`이라는 매직 넘버가 있다. `lookback=20`은 21일로 반올림된다. | 그래프 끝에 `strategy.*` 블록을 두고 `result` 포트를 그대로 쓰는 범용 `run_graph_backtest(graph, dates, run_context)`로 일반화한다. |
| 6 | 시각 상수 `_CLOSE`/`CLOSE`/`OPEN` (n08, n11, n15) | `21:00`, `14:30 UTC`가 세 곳에 하드코딩돼 있다. 미국 서머타임(3–11월)에는 장 마감이 20:00 UTC, 개장이 13:30 UTC다. | 거래소 캘린더 모듈 하나로 모으고 `zoneinfo("America/New_York")`로 계산한다. |
| 7 | `web/src/App.tsx` `connectAll` | `port.type`이 같은지만 보고 자동 연결하므로 `accepts`/`mirrors`/`same_type_as`를 무시한다. `function_catalog()`도 이 필드와 `role`, `required_data`, `version`을 내보내지 않는다. 편집기 연결과 `validate_graph` 결과가 다를 수 있다. | 카탈로그에 해당 필드를 추가하고 연결 규칙을 하나로 맞춘다. 가장 단순한 방법은 Python 검증을 API로 노출하는 것이다. |

### 🟡 설계·유지보수

| # | 문제 | 제안 |
|---|---|---|
| 8 | **`UniverseFrame` 클래스가 두 개**다 (`n01`: `as_of: str` + rows, `n07`: `as_of: datetime` + symbols). `n11`이 `OrderUniverse` 별칭으로 변환한다. 이름이 같아 import 실수가 나기 쉽다. | 하나로 합치거나(시각은 `datetime` 하나로) 이름을 구분한다(`OrderUniverse`). 날짜 표현도 `str`과 `datetime`이 섞여 있으니 하나로 정한다. |
| 9 | **바 타입이 두 개**다 (`DailyBar`는 OHLCV + `date: str`, `MarketBar`는 open·close + 시각). n08·n15에서 매번 변환한다. | `MarketBar`에 high/low/volume을 넣고 로더가 바로 `MarketBar`를 만들게 한다. |
| 10 | 번호와 의존 방향이 어긋난다. `n05_signals`가 `n07_runtime`을 import한다(`MarketBar`, `StrategyValidationError`). | 공용 타입·예외를 낮은 계층(`n01`/`n02` 또는 `types.py`, `errors.py`)으로 옮긴다. 장기적으로는 `nXX_` 접두어 대신 하위 패키지(`core/`, `blocks/`, `graph/`, `backtest/`)로 나누는 것이 Python 관례에 맞다. 번호는 노트북 학습 순서용이라면 README에 순서를 적는 것으로 충분하다. |
| 11 | 예외 타입이 섞여 있다. 프레임·블록은 `ValueError`, 런타임은 `StrategyValidationError`, 엔진은 그래프 오류를 `ValueError(reason)`로 던진다. | `StrategyValidationError`를 기반으로 `GraphValidationError`, `DataInsufficientError` 등으로 나누면 UI가 오류 종류별로 처리할 수 있다. |
| 12 | `validate_params`의 교차 규칙이 키 이름(`fastDays`, `skipMonths`)으로 하드코딩돼 있다. `momentum.trend`의 `price_above_ma` 방식은 `fastDays`를 쓰지 않는데도 검사된다. | `FunctionSpec`에 `constraints`(또는 검증 함수)를 두어 블록별로 선언한다. |
| 13 | 카탈로그 정의가 이원화돼 있다 (`_LEGACY_FUNCTIONS` + `replace()`로 `role` 덧입히기 + `_SCORE_IDS` + `_REQUIRED_DATA`). | 마이그레이션이 끝났으면 26개 모두 같은 형식으로 `role`·`required_data`를 직접 적는다. 블록 정의를 YAML/JSON으로 분리하면 웹 `layers.ts`와도 공유할 수 있다. |
| 14 | `web/src/layers.ts`가 블록 ID를 하드코딩해 블록을 추가하면 두 곳을 고쳐야 한다. | 층을 `role`(또는 `category`)에서 파생하거나 카탈로그에 `layer` 필드를 둔다. |
| 15 | 성능: `_upto`가 매번 전체 시계열을 선형 탐색하고, `moving_average_cross`는 `as_of`와 상관없이 전체 이력을 매번 다시 계산하며, `_beta_scores`는 `day in market_days`(list)로 O(N·D·W)다. 백테스트에서 날짜 수에 대해 제곱으로 느려진다. 1년 데이터에서는 문제없다(0.4초). | `bisect`로 `as_of` 위치 찾기, 이동평균 누적합, `market_days`를 `set`으로. 데이터가 커지면 numpy/pandas 백엔드 도입을 검토한다. |
| 16 | `n11` 핸들러의 `context: object` + `getattr`로 타입 안정성이 없고 `# type: ignore`가 많다. | `context: ExecutionContext`로 타입을 지정한다(순환 import는 `TYPE_CHECKING`으로 피한다). 입력 타입은 `_expect(inputs, "score", ScoreFrame)` 같은 헬퍼로 일관되게 검사한다. |

### 🟢 정리

| # | 내용 |
|---|---|
| 17 | Git: 기존 `ai_studio/*.py` 8개가 삭제되고 `n01~n16`이 untracked 상태다. `git add -A`로 한 번에 커밋하면 Git이 rename으로 인식해 이력을 유지할 가능성이 높다. `web/`, `tests/test_*.py` 3개, `scripts/`, `notebooks/`도 아직 커밋되지 않았다. |
| 18 | `data/sp500_ohlc_1y.csv`(12MB)를 커밋할지 정해야 한다. 재생성 가능하면 `.gitignore`에 넣고 `scripts/fetch_yahoo_ohlc.py`로 받게 하는 것이 낫다. 루트의 빈 `package-lock.json`은 삭제 대상이다. |
| 19 | `pyproject.toml`에 의존성·빌드 백엔드·테스트 설정이 없다. 스크립트와 노트북은 `PYTHONPATH=.`에 의존한다. `[build-system]`을 추가하고 `pip install -e .`로 쓰며, `yfinance`, `pandas`, `jupyter`는 optional extras로 둔다. |
| 20 | 문서 불일치: `n08_blocks` 독스트링이 옛 이름 `functions.py`를 가리키고, README의 "14개 블록"은 현재 26개와 다르다. `n16_self_check`의 `26` 하드코딩은 블록을 추가할 때마다 고쳐야 한다. |
| 21 | 중복 코드: `n11._target_weight`가 `n08.allocate_target_weight`와 같다. `_CLOSE` 상수가 n08과 n11에 있다. `weight_by_score`는 `None`을 검사한 뒤에 다시 필터링한다. |
| 22 | `__init__.py`가 `n07_runtime`(StrategyResult, generate_orders 등)과 `n09_backtest`를 내보내지 않아 공개 API 범위가 애매하다. |
| 23 | 테스트 공백: `n15`은 테스트가 1개뿐이다. 스펙↔핸들러 정합성(모든 블록이 기본 파라미터로 실행되는지, 출력 타입이 스펙과 맞는지)을 매개변수화 테스트로 추가하면 2번과 1번 같은 문제를 자동으로 잡는다. |

### 추천 진행 순서

1. **17·18 커밋 정리** — 현재 상태를 먼저 고정한다.
2. **23 정합성 테스트 추가** → 1·2·3을 고친다 (작고 효과가 크다).
3. **8·9·10 타입 통합** — 이후 작업의 기반이다.
4. **5 범용 그래프 백테스트** + 6 캘린더.
5. **7·14 웹-백엔드 계약 통일** (검증 API).

---

## 6. 목표 구조

### 6.1 진단 (현재 구조의 한계)

출력 타입별로 그 출력을 입력으로 받는 블록을 세면 다음과 같다.

| 출력 타입 | 소비 블록 |
|---|---|
| `target_portfolio` | 없음 |
| `strategy_result` | 없음 (주문·체결은 코드에서 직접 호출) |
| `indicator` | `condition.threshold`뿐 |
| `event` | `strategy.event`, `strategy.hybrid`뿐 (생산도 `momentum.ma-cross` 하나) |

- **팩터 갈래**(유니버스 → 점수 → 선정 → 비중 → 제약 → 목표)는 `portfolio.target`에서 끝나고, **시그널 갈래**(유니버스 → 지표 → 조건·이벤트 → 논리 → 전략 판단)는 `strategy.*`에서 끝난다. 둘 다 그래프만으로는 체결 원장에 닿지 않는다. `n15`는 `StrategyResult`를 직접 만들어 이 끝을 우회한다.
- 빠진 단계:
  1. **점수 합성**: 점수 두 개를 비율로 합치는 블록이 없다. IF-ELSE는 둘 중 하나만 고른다.
  2. **청산 이벤트**: 손절·익절은 비중을 0으로 만드는 제약이라, 전략 판단이 소비하는 이벤트가 아니다.
  3. **매매 시점**: 리밸런스 주기와 이탈 허용이 비중 상한·현금 같은 제약 역할에 들어 있고, 화면의 공통 실행 조건과도 겹친다. "언제 살지"와 "얼마까지 담을지"가 한 역할에 섞여 있다.
  4. **보유 유지**: 선정이 평가일마다 상위 N을 다시 고른다. 순위가 조금 밀려도 보유를 유지하는 버퍼가 없다.
- 지표는 RSI만 독립 블록이다. 이동평균, 수익률, 변동성은 조건·점수 블록 안에서만 계산되어 다른 블록이 다시 쓰지 못한다.
- 현금 비중이 고정이라 "지수가 이동평균 아래이면 현금으로 쉰다" 같은 **국면 전환**을 표현할 수 없다.

### 6.2 원칙

1. **MECE 역할**: 모든 노드는 아래 단계 중 정확히 하나의 역할만 가진다. 여러 역할을 한 노드에 넣지 않는다.
2. **하나의 캔버스**: 팩터 갈래와 시그널 갈래를 따로 두지 않는다. 같은 단계 열을 공유하고, 전략은 필요한 열만 쓴다.
3. **원자 노드 + 프리셋**: 자주 쓰는 조합(예: 골든크로스 = 이동평균 2개 → 비교 → 상태 전이)은 저장된 하위 그래프(프리셋)로 팔레트에 따로 둔다.
4. **단일 종착점**: 모든 유효한 그래프는 DECISION 노드 하나 → EXECUTION 노드 하나로 끝난다. 백테스트는 `graph → execute → 원장` 한 경로만 쓴다.
5. **정책 소유자 하나**: 매매 주기는 실행 조건(RUN_CONTEXT)과 EXECUTION 노드만 정한다.

### 6.3 단계와 노드

| 단계 | 역할 | 입력 → 출력 | 노드 (기존 / **신규**) |
|---|---|---|---|
| 0 | 실행 조건 (노드 아님) | — | 기간, 신호·리밸런스 주기, 자금, 비용 |
| 1 | UNIVERSE | universe → universe | compose, exclude, min-price, set-op(universe), **조건 필터**(universe + condition → universe) |
| 2 | INDICATOR | universe → indicator | rsi, **수익률, 이동평균, 변동성, 기간 고점, 가격, 재무값**. 판단 없이 숫자만 낸다. **시장 범위 지표**(지수·동일가중 시장)도 같은 역할 |
| 3a | SCORE | indicator → score | **점수화**(방향·순위·z-score), **점수 합성**(가중합) |
| 3b | CONDITION | indicator → condition | threshold, **지표 간 비교**, combine, **NOT**, if-else |
| 4a | SELECTION | score·condition → selection | top-n(**보유 유지 버퍼** 포함), threshold, **조건 선정** |
| 4b | EVENT | condition·가격 → event | **상태 전이**(거짓→참 진입, 참→거짓 청산), **손절·익절·트레일링**, **보유기간 청산** |
| 5 | WEIGHTING | selection → weight | target-weight(동일·점수), **역변동성** |
| 6 | CONSTRAINT | weight → weight | max-weight, cash, **국면 전환**(weight + 시장 condition → weight) |
| 7 | TARGET | weight → target_portfolio | portfolio.target |
| 8 | DECISION | → strategy_result | **state**(target_portfolio 입력), **event**(이벤트 + 선택 게이트 + 사이징) |
| 9 | EXECUTION | strategy_result → 원장 | **실행 노드**: 리밸런스 정책(주기·이탈 허용) + 주문 수량 + 다음 시가 체결 + 수수료 |

### 6.4 기존 노드 재분류

| 기존 노드 | 섞인 역할 | 목표 형태 |
|---|---|---|
| `momentum.factor` | 지표 + 점수 | 수익률 지표 → 점수화 |
| `factor.low-volatility` | 지표 + 점수 | 변동성 지표 → 점수화(낮을수록 좋음) |
| `factor.value`, `factor.quality` | 재무값 + 점수 | 재무 지표 → 점수화 |
| `momentum.trend` | 지표 + 조건 | 이동평균 지표 → 지표 간 비교 |
| `momentum.breakout` | 지표 + 조건 | 기간 고점 지표 → 지표 간 비교 |
| `momentum.ma-cross` | 지표 + 조건 + 이벤트 | 프리셋 "골든크로스" |
| `allocation.stop` | 이벤트를 제약으로 표현 | EVENT의 손절·익절 |
| `allocation.rebalance` | 매매 시점을 제약으로 표현 | EXECUTION의 리밸런스 정책 |
| `strategy.state` | 조건 → 동일비중을 내부에서 계산 | target_portfolio 입력 |
| `strategy.event` + `strategy.hybrid` | 사이징 없음, 게이트가 조건뿐 | 하나로 통합, 게이트는 condition 또는 selection, 사이징 파라미터 |

### 6.5 완료 기준 (커버리지 테스트)

아래 전략이 노드만으로, 직접 코드 없이 원장까지 실행되어야 한다.

| 전략 | 경로 |
|---|---|
| 모멘텀 상위 10, 월간 리밸런싱 | 1 → 2 → 3a → 4a → 5 → 6 → 7 → 8(state) → 9 |
| 모멘텀 + 저변동성 합성 점수 | 2 ×2 → 3a(점수화 ×2, 합성) → 4a → … |
| 상위 N + 보유 유지 버퍼 | … → 4a(top-n, buffer) → … |
| 추세 필터 후 팩터 선정 | 2 → 3b → 1(조건 필터) → 2 → 3a → … |
| 시장이 이동평균 아래면 현금 | … → 6(국면 전환 ← 시장 지표 → 3b) → 7 → … |
| RSI < 30 진입, 손절 10% | 2 → 3b → 4b(상태 전이) + 4b(손절) → 8(event) → 9 |
| 골든크로스 / 데드크로스 | 2 ×2 → 3b(비교) → 4b → 8(event) → 9 |
| 상위 N 중 골든크로스만 진입 | 4a를 게이트로 + 4b → 8(event) → 9 |

### 6.6 진행 순서

0. 현재 상태 커밋, **타입 통합**: `UniverseFrame` 하나, 바 타입 하나(`MarketBar`에 OHLCV), 시각은 tz 있는 `datetime` 하나, 거래소 시각은 `America/New_York`에서 계산, 공용 예외를 하위 계층으로 이동
1. **팩터 갈래를 원장에 연결**: `strategy.state` 입력을 target_portfolio로, EXECUTION 노드 추가, `n15`를 그래프 경로로 교체
2. **원자 노드**: 지표 분리(수익률·이동평균·변동성·고점·가격·시장), 점수화·합성, 지표 비교·NOT, 상태 전이, 조건 필터·조건 선정, 보유 유지 버퍼, 국면 전환
3. **DECISION 통합과 이동**: event·hybrid 통합(게이트·사이징), 손절·익절을 EVENT로, 리밸런스를 EXECUTION으로, 기존 복합 노드는 프리셋으로
4. **커버리지 테스트**: 6.5의 전략 전부
5. **캔버스·편집기**: 단계 열을 `role`에서 파생, 카탈로그에 연결 규칙 필드 추가, 종착점 검사
