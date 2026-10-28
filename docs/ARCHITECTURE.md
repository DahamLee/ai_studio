# AI STUDIO 코드 구조 명세

> 기준: `refactor/unified-pipeline` 브랜치 (2026-10-28). 테스트 75개 통과, `python -m ai_studio.n16_self_check` 통과.
> 처음 진단한 구조와 개선 과정은 5장, 목표 구조와 완료 기준은 6장에 있다.

## 1. 개요

노코드 전략 편집기의 **블록 계약 + 연결 검사 + 그래프 실행기 + 참조 백테스트 원장**을 순수 Python(표준 라이브러리만)으로 구현한 저장소다. 웹(`web/`)은 블록 팔레트와 캔버스를 그리는 React 편집기다.

```
ai-studio/
├── ai_studio/          # 핵심 패키지 (n01 ~ n17, 약 3,750줄)
├── tests/              # unittest 75개
├── scripts/            # 데이터 수집, 실행 예제
├── notebooks/          # 01 전체 흐름, 02 종목 하나
├── data/sp500_ohlc_1y.csv   # S&P 500 503종목 × 251거래일 일봉
├── docs/ARCHITECTURE.md
└── web/                # Vite + React 19 + TS 편집기
```

## 2. 전체 흐름

```
[편집기 web/] ──catalog.json── n10.function_catalog()   (단계, 블록, 프리셋, 예제, 실행 조건)
      │        ──/api/validate── n17 → n13.validate_strategy
      │        ──/api/backtest── n17 backtest → n15.run_graph_backtest(저장된 일봉)
      ▼  StrategyGraph(nodes, edges)
n15.run_graph_backtest(graph, history, RunSettings)
      │  거래일마다
      ├─ Ledger.mark(종가 평가)
      └─ 평가일이면 n14.execute(graph, ExecutionContext(as_of, history, positions))
             │  위상 정렬 → n11 핸들러 → n08 블록 / n04 지표 / n05 조건·이벤트 / n06 점수·선정·비중
             │  UNIVERSE → INDICATOR → SCORE·CONDITION → SELECTION·EVENT → WEIGHT·CONSTRAINT → TARGET
             ▼
          STRATEGY(strategy.state ← target_portfolio | strategy.event ← event_plan) → StrategyResult
             ▼
          EXECUTION(execution.orders 정책) → n09.Ledger.step → n07.generate_orders → fill_at_next_open
      ▼
BacktestResult(signal_history, trade_log, position_history, portfolio_history, skipped_orders)
```

## 3. 모듈별 명세

모든 모듈은 자기보다 번호가 작은 모듈만 import한다(카탈로그가 프리셋을 싣는 지연 import 두 곳 제외).

| 모듈 | 역할 | 주요 내용 |
|---|---|---|
| `n01_frames` | 기본 타입 | `StrategyValidationError`, `MarketBar`(OHLCV·`timestamp`·`available_at`·`open_at`), `UniverseFrame(as_of, symbols)`, Score·Indicator·Condition·Event·Selection·Weight 프레임, `TargetPortfolio` |
| `n02_schema` | 블록 계약 | `PortSpec`(accepts·same_type_as·mirrors), `ParamSpec`, `FunctionSpec`(role·needs_previous·lookback), `STAGES`(역할→단계 띠), `RUN_CONTEXT`, 그래프 타입 |
| `n03_market_data` | 시세·거래소 시각 | `load_ohlc`, `daily_bar`, `history_from_bars`(중복 거부), `bars_until`(bisect), `session_open/close/date`(America/New_York) |
| `n04_indicators` | 순수 지표 수학 | `moving_average`, `window_high`, `rsi`, `total_return`, `daily_returns`, `stdev` |
| `n05_signals` | 조건·이벤트 | `compare_threshold`, `compare_indicators`(MARKET 펼침), `negate`, `transition_events` |
| `n06_portfolio` | 점수 이후 단계 | `score_from_indicator`(raw·rank·zscore), `combine_scores`, `select_top_n(_with_buffer)`, `select_min_score`, `select_matched`, `weight_equal/by_score/by_indicator`, `cap_max_weight`, `apply_cash_weight`, `scale_when_false`, `to_target_portfolio` |
| `n07_runtime` | 판단·주문·체결 | `AssetSignal`, `StrategyResult`, `PositionState`, `evaluate_state(targets)`, `evaluate_events(max_positions, priority)`, `generate_orders`, `fill_at_next_open(shortfall)` |
| `n08_blocks` | 시세 기반 블록 | 유니버스(compose·exclude·min-price), 지표(return·moving-average·price·high·volatility·rsi, `scope`), `market_series`(동일가중 합성 지수), 손절·보유기간 청산 이벤트, 논리 연산 |
| `n09_backtest` | 원장 | `Ledger.step/mark/result`, `run_backtest`(미리 계산한 판단용), `trade_rows`, `write_trades_csv` |
| `n10_functions` | 블록 카탈로그 33개 | 단계별 정의, `get_function`, `function_catalog()` |
| `n11_registry` | 블록 id → 핸들러 | `register`, `get_handler`, `ExecutionContext` 타입의 핸들러 33개 |
| `n12_strategies` | 프리셋·예제 | `PRESETS` 8개, `place()`, 예제 전략 `EXAMPLES` 8개, `graph_dict/graph_from_dict` |
| `n13_connect` | 연결 검사 | `can_connect`, `validate_graph`, `validate_strategy`(실행 노드 1개·닿지 않는 블록 경고), `check_editor_graph` |
| `n14_engine` | 그래프 실행기 | `ExecutionContext`, `execute`(직전 거래일 상류 재실행, 빈 결과 경고), `execution_order` |
| `n15_ohlc_backtest` | 그래프 백테스트 | `RunSettings`, `period_ends`, `run_graph_backtest`, `run_momentum_topn` |
| `n16_self_check` | 정합성 점검 | 카탈로그=레지스트리, 프리셋·예제 유효성, 핵심 연결 규칙 |
| `n17_editor_api` | 편집기 진입점 | 표준입력 그래프 JSON → 검증 결과 JSON, `backtest` 인자면 백테스트 요약(수익률·낙폭·평가액 추이)과 매매 내역 전체(`trade_rows`) |

### 3.1 블록 (33개)

캔버스에 보이는 이름은 "○○하기" 형태다. 단계 이름도 같은 형태로 `n02.STAGES`에 있다.

| 단계 | 역할 | 블록 (화면 이름) |
|---|---|---|
| 1 종목 고르기 | UNIVERSE | `universe.compose` 종목 불러오기, `universe.exclude` 투자 제외하기, `universe.min-price` 저가주 빼기 |
| 2 지표 계산하기 | INDICATOR | `indicator.return` 수익률 구하기, `.moving-average` 이동평균 구하기, `.price` 가격 가져오기, `.high` 기간 고점 구하기, `.volatility` 변동성 구하기, `.rsi` RSI 구하기, `.fundamental` 재무 지표 가져오기* |
| 3 평가하기 | SCORE | `score.from-indicator` 점수 매기기, `score.combine` 점수 합치기 |
| | CONDITION | `condition.threshold` 기준값과 비교하기, `condition.compare` 두 지표 비교하기 |
| | LOGIC | `logic.combine` 조건 묶기, `logic.not` 조건 뒤집기, `logic.if-else` 조건에 따라 나누기 |
| 4 매매 대상·시점 정하기 | SELECTION | `selection.top-n` 상위 N개 고르기(대상 조건, 보유 유지 버퍼), `selection.threshold` 점수 기준으로 고르기, `selection.condition` 조건 맞는 종목 고르기 |
| | EVENT | `event.transition` 진입·청산 시점 잡기, `event.stop` 손절·익절 시점 잡기, `event.holding-period` 보유기간 만료 시점 잡기 (뒤 둘은 보유 종목만 보므로 입력 없음) |
| 5 비중 정하기 | WEIGHTING | `allocation.target-weight` 비중 나누기, `allocation.indicator-weight` 지표로 비중 나누기, `allocation.event-slots` 최대 보유 수로 비중 정하기(진입·청산 신호, 게이트, 최대 보유 수 → `event_plan`) |
| | CONSTRAINT | `portfolio.max-weight` 비중 상한 두기, `portfolio.cash` 현금 남기기, `portfolio.risk-off` 국면 따라 줄이기 |
| | TARGET | `portfolio.target` 목표 포트폴리오 만들기 |
| 6 매매 결정하기 | STRATEGY | `strategy.state` 목표 비중대로 매매 정하기(← 목표 포트폴리오), `strategy.event` 신호대로 매매 정하기(← 진입·청산 계획) |
| 7 주문하기 | EXECUTION | `execution.orders` 다음 날 시가에 주문하기(리밸런스 주기, 이탈 허용, 소수 주문, 현금 부족 처리) |

4단계(매매 대상·시점)와 6단계(매매 결정)의 차이: 4단계는 보유·현금을 모른 채 전체 종목에 대해 "지금이 그 시점이다"만 알린다. 6단계는 현재 보유와 남은 자리를 보고 실제로 살 것·팔 것·둘 것을 정한다. 7단계는 그 결정을 주식 수로 바꿔 다음 거래일 시가에 체결한다. 4단계를 대상·시점 두 단계로 나누지 않는 이유는 둘이 서로 다른 갈래(팩터·시그널)의 같은 순서이기 때문이다. 나누면 팩터 갈래(선정 → 비중)와 시그널 갈래(조건 → 시점) 모두 빈 띠를 건너뛰게 된다.

**선 규칙** (`n13.check_stage_order`, 편집기 `stageOrderProblem`): 선은 앞 단계에서 뒤 단계로만 간다. 주 입력은 같은 단계나 바로 앞 단계에서만 받는다. 보조 입력(`PortSpec.auxiliary`)만 단계를 건너뛴다 — 국면 따라 줄이기의 투자 조건(3→5), 지표로 비중 나누기의 지표(2→5), 최대 보유 수로 비중 정하기의 게이트. 편집기는 보조 입력 선을 점선으로 그린다. 판단 노드는 유니버스를 받지 않고, 앞 단계 데이터(목표 종목·신호 종목)와 현재 보유로 판단 대상을 정한다.

\* 재무 데이터가 없어 실행하면 오류를 낸다.

### 3.2 프리셋 (8개)

모멘텀 점수, 저변동성 점수, 가치 점수, 추세 조건, 신고가 돌파, 골든크로스·데드크로스, 시장 국면, RSI 과매도. 기존 복합 블록(`momentum.factor`, `momentum.trend`, `momentum.breakout`, `momentum.ma-cross`, `factor.*`)이 여기로 옮겨졌다.

### 3.3 웹 편집기

- 카탈로그의 `stages`로 띠 7개를 만들고, 블록은 `stage`(역할에서 파생) 띠 안에서만 움직인다.
- 선은 상태로 들고 있다. 블록을 놓으면 단계 규칙에 맞는 앞 노드와 한 번 이어 주고, 이후에는 출력 포트 → 입력 포트 클릭으로 잇고 선 클릭으로 끊는다. 단계를 거스르거나 건너뛰는 선은 이유와 함께 거절한다.
- 프리셋 추가, 예제 불러오기, 개발 서버 `/api/validate`를 통한 Python 검증(연결 오류 / 완성 전 / 실행 가능 + 경고).
- 흐름은 위에서 아래다. 입력 포트는 노드 위, 출력 포트는 노드 아래에 있고, 다른 단계로 가는 선은 아래로 내려간다. 같은 단계 안의 선(예: 종목 불러오기 → 투자 제외하기)은 옆으로 잇는다.
- 자동 정렬: 띠마다 한 줄로 놓고, 노드를 앞 단계 노드의 바로 아래 열에 둔다. 같은 단계의 앞 노드보다는 오른쪽에 두고, 열이 차 있으면 오른쪽 빈 열로 민다. 예제·프리셋을 놓을 때와 상단 버튼에서 쓴다. 노드를 하나 추가하면 이어진 앞 노드 바로 아래에 놓는다.
- 파라미터 편집: 오른쪽 패널에서 선택한 노드의 파라미터를 타입(number·integer·enum·boolean·date)에 맞는 입력으로 바꾼다. 왼쪽 실행 조건도 카탈로그의 `runContext`로 그린다(날짜를 비우면 저장된 일봉 전체).
- 백테스트 하기: 실행 가능한 그래프일 때만 켜지고 `/api/backtest`로 돌린다. 오른쪽 패널에 요약(수익률·낙폭·평가액 추이)을, 캔버스 아래에 매매 내역 전체를 노트북 DataFrame 모양의 표(`TRADE_COLUMNS`, 행 번호 포함)로 보이고 CSV로 내려받을 수 있다. 실행 뒤 그래프를 바꾸면 결과가 이전 그래프 기준임을 표시한다.

## 4. 설계 원칙

1. **역할 하나**: 블록마다 역할이 하나다. 여러 단계를 묶은 것은 프리셋이다.
2. **하나의 캔버스**: 팩터·시그널 갈래가 같은 단계 띠를 쓰고, 둘 다 STRATEGY → EXECUTION으로 끝난다.
3. **단일 경로**: 모든 백테스트는 `graph → execute → Ledger` 한 경로다. 전략별 코드가 없다.
4. **정책 소유자 하나**: 매매 시점(리밸런스 주기·이탈 허용)은 실행 노드만 정한다. 평가 주기는 실행 조건이 정한다.
5. **미래 참조 방지**: 블록은 `bars_until(as_of)`로 공개된 봉만 본다. 신호는 t 종가, 체결은 이후 시가, 평가는 그날 종가다.
6. **조용히 실패하지 않기**: 데이터가 모자라 빈 지표·점수는 필요한 봉 수와 함께 경고하고, 체결 못 한 주문은 `skipped_orders`에 남긴다.

## 5. 처음 진단한 개선점과 처리 결과

| # | 처음 문제 | 결과 |
|---|---|---|
| 1 | 기본 모멘텀 블록이 1년 데이터에서 점수 0행을 조용히 반환 | 필요 봉 수를 파라미터로 계산하고 빈 결과를 경고 |
| 2 | IF-ELSE 스펙이 universe·weight를 받는다고 했지만 구현 없음 | 허용 타입을 condition·score로 맞춤 |
| 3 | `allocation.rebalance`의 `cadence` 미사용, 주기 정의 중복 | 블록 삭제, 실행 노드가 유일한 주기 소유자 |
| 4 | 이벤트 진입 비중 1/오늘 진입 수로 초과 배정 | 최대 보유 수 기반 슬롯과 우선순위 |
| 5 | `n15`가 StrategyResult를 직접 만듦 | `run_graph_backtest` 단일 경로 |
| 6 | UTC 고정 시각(서머타임 미반영) | 뉴욕 거래소 시각으로 계산 |
| 7 | 웹 자동 연결이 `accepts` 등을 무시 | 명시적 선 + Python 검증 API |
| 8–9 | `UniverseFrame`·바 타입 이중 정의 | n01 하나로 통합 |
| 10 | n05 → n07 역방향 의존 | 모든 의존이 아래 번호로 |
| 12 | 파라미터 교차 검증을 키 이름으로 하드코딩 | 해당 블록이 사라져 제거 |
| 13 | 카탈로그 이원화(legacy + replace) | 한 형식으로 다시 씀 |
| 14 | `layers.ts` 블록 id 하드코딩 | `STAGES`와 role에서 파생 |
| 15 | `_upto` 선형 탐색, 베타 계산 O(N·D·W) | bisect, 집합 탐색 |
| 16 | 핸들러 `context: object` + getattr | `ExecutionContext` 타입 |
| 17 | 커밋되지 않은 리네임 | 사용자 커밋 후 브랜치에서 단계별 커밋 |
| 20–22 | 문서 불일치, 중복 코드, 공개 API | 정리 |
| 23 | 스펙↔핸들러 정합성 테스트 없음 | self-check와 커버리지 테스트 |

진행 중 새로 발견해 고친 것:

- `universe.compose`가 기준일에 아직 상장하지 않은 종목을 넣던 미래 참조.
- 마지막 리밸런스 이후 수익이 평가에 빠지던 문제(거래일마다 평가로 해결).
- 다음 시가 급등 시 체결기가 예외로 멈추던 문제(실행 노드 `shortfall` 정책).
- 거래대금 단위가 `억원`으로 표시되던 문제(시세 통화).
- 같은 종목·시각 봉이 조용히 덮어써지던 문제(거부).

**남은 것**

- 재무 데이터(가치·퀄리티), 시가총액, 업종 데이터 연결.
- 실제 지수 시세. 지금 시장 범위 지표는 적재 종목의 동일가중 합성 지수다.
- 휴장일 달력, 기업행동(분할·배당), 부분체결, 세금.
- `pyproject.toml`에 선택 의존성(시세 수집용 pandas·requests·yfinance, 노트북용 ipykernel)과 빌드 설정이 없다. 지금 `.venv`는 손으로 만든 상태라 다른 환경에서 재현되지 않는다.
- 일봉 CSV(`data/sp500_ohlc_1y.csv`)는 커밋돼 있다. 데이터를 늘리면 저장소 밖으로 뺄지 정해야 한다.
- 편집기: 백테스트는 개발 서버(`npm run dev`)에서만 돈다. 정적 빌드에는 API가 없다. 실행마다 CSV를 다시 읽어 약 3초 걸린다.
- `event.transition`은 직전 거래일 판단에 현재 보유를 그대로 쓴다. 보유에 따라 달라지는 조건(버퍼 선정 등)을 전이에 넣으면 근사가 된다.

## 6. 목표 구조

### 6.1 진단 (처음 구조의 한계)

출력 타입별로 그 출력을 입력으로 받는 블록을 세면 다음과 같았다.

| 출력 타입 | 소비 블록 |
|---|---|
| `target_portfolio` | 없음 |
| `strategy_result` | 없음 (주문·체결은 코드에서 직접 호출) |
| `indicator` | `condition.threshold`뿐 |
| `event` | `strategy.event`, `strategy.hybrid`뿐 (생산도 `momentum.ma-cross` 하나) |

- **팩터 갈래**(유니버스 → 점수 → 선정 → 비중 → 제약 → 목표)는 `portfolio.target`에서 끝나고, **시그널 갈래**(유니버스 → 지표 → 조건·이벤트 → 논리 → 전략 판단)는 `strategy.*`에서 끝났다. 둘 다 그래프만으로는 체결 원장에 닿지 않았다.
- 빠진 단계: **점수 합성**, **청산 이벤트**, **매매 시점**(주기·이탈이 제약 역할에 섞임), **보유 유지 버퍼**.
- 지표는 RSI만 독립 블록이었고, 현금 비중이 고정이라 **국면 전환**을 표현할 수 없었다.

### 6.2 원칙

1. **MECE 역할**: 모든 노드는 아래 단계 중 정확히 하나의 역할만 가진다.
2. **하나의 캔버스**: 팩터 갈래와 시그널 갈래가 같은 단계 열을 공유하고, 전략은 필요한 열만 쓴다.
3. **원자 노드 + 프리셋**: 자주 쓰는 조합은 저장된 하위 그래프(프리셋)로 둔다.
4. **단일 종착점**: 모든 유효한 그래프는 DECISION 노드 하나 → EXECUTION 노드 하나로 끝난다.
5. **정책 소유자 하나**: 매매 주기는 EXECUTION 노드만 정한다.

### 6.3 단계와 노드

| 단계 | 역할 | 입력 → 출력 | 노드 |
|---|---|---|---|
| 0 | 실행 조건 (노드 아님) | — | 기간, 평가 주기, 자금, 비용 |
| 1 | UNIVERSE | universe → universe | compose, exclude, min-price |
| 2 | INDICATOR | universe → indicator | return, moving-average, price, high, volatility, rsi, fundamental (종목·시장 범위) |
| 3a | SCORE | indicator → score | from-indicator(방향·백분위·표준점수), combine |
| 3b | CONDITION·LOGIC | indicator → condition | threshold, compare, combine, not, if-else |
| 4a | SELECTION | score·condition → selection | top-n(보유 유지 버퍼), threshold, condition |
| 4b | EVENT | condition·보유 → event | transition, stop(손절·익절·트레일링), holding-period |
| 5 | WEIGHTING | selection → weight, event → event_plan | target-weight(동일·점수), indicator-weight(역변동성), event-slots(최대 보유 수·게이트) |
| 6 | CONSTRAINT | weight → weight | max-weight, cash, risk-off(국면 전환) |
| 7 | TARGET | weight → target_portfolio | portfolio.target |
| 8 | DECISION | → strategy_result | state(target_portfolio), event(event_plan) |
| 9 | EXECUTION | strategy_result → 원장 | execution.orders |

### 6.4 완료 기준 (커버리지 테스트) — 모두 통과

`tests/test_coverage.py`. 저장된 일봉으로 8개 모두 원장까지 실행하고, 합성 시세로 국면 전환과 손절을 확인한다.

| 전략 | 경로 | 1년 결과* |
|---|---|---|
| 모멘텀 상위 10, 월간 리밸런싱 | 1 → 2 → 3a → 4a → 5 → 6 → 7 → 8(state) → 9 | +44.30% |
| 모멘텀 + 저변동성 합성 점수 | 2 ×2 → 3a(백분위 ×2, 합성) → 4a → … | −10.52% |
| 상위 N + 보유 유지 버퍼 | … → 4a(top-n, buffer) → … | +47.80% (체결 100건, 버퍼 없으면 109건) |
| 추세 필터 후 팩터 선정 | 2 → 3b(추세) + 3a(모멘텀) → 4a(top-n, 대상 조건) → … | +37.04% |
| 시장이 이동평균 아래면 현금 | … → 6(risk-off ← 시장 지표 → 3b) → 7 → … | −1.79% |
| RSI < 30 진입, 손절·익절·보유기간 | 2 → 3b → 4b(전이) + 4b(손절) + 4b(보유기간) → 5(보유 자리) → 8(event) → 9 | −8.28% |
| 골든크로스 / 데드크로스 | 2 ×2 → 3b(비교) → 4b(전이) → 5(보유 자리) → 8(event) → 9 | +10.75% |
| 상위 N 중 골든크로스만 진입 | 4a를 게이트로 + 4b → 5(보유 자리) → 8(event) → 9 | +7.93% |

\* 2025-09-30 ~ 2026-09-29, 초기 10만 달러, 수수료 0.1%, 슬리피지 0.05%, 매일 평가. 기간이 1년이라 비교용 수치일 뿐이다.

### 6.5 진행 순서 — 완료

| 단계 | 내용 | 커밋 |
|---|---|---|
| 0 | 타입 통합, 거래소 시각 | `69b16db` |
| 1 | 팩터 갈래를 원장에 연결 (state 입력, 실행 노드, 그래프 백테스트) | `5bb4920` |
| 2 | 원자 노드 (지표·점수·조건·이벤트·버퍼·국면 전환) | `8c4295c` |
| 3 | 판단 통합, 손절·리밸런스 이동, 복합 블록 → 프리셋 | `965af23` |
| 4 | 커버리지 테스트, 현금 부족 정책 | `eebbdb3` |
| 5 | 캔버스 단계 띠·명시적 선·Python 검증 | `1fd5310` |
| 6 | "○○하기" 이름, 단계 순서 선 규칙, 최대 보유 수로 비중 정하기 노드, `universe.filter`·`logic.set-op` 제거 | 이번 커밋 |
