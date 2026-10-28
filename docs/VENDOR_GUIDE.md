# 전략 캔버스 개발 가이드 (수행사용)

이 문서는 노코드 전략 편집기를 서비스로 개발할 수행사 개발자와 PM을 위한 안내서다. 이 저장소(브랜치 `refactor/unified-pipeline`, PR #2)는 **참조 구현**이다. 노드 계약, 연결 규칙, 실행 순서, 백테스트 원장이 순수 Python(표준 라이브러리만)으로 구현되어 있고, 테스트가 그 동작을 고정한다. 서비스 구현은 언어와 구조를 바꿔도 되지만, 아래 **계약**(노드 정의, 그래프 JSON, 연결 규칙, 시점 규칙)은 지켜 주기를 요청한다.

| 문서 | 내용 |
|---|---|
| 이 문서 | 개념, 노드 정의·레지스트리, 데이터와 조인, 새 노드 추가 절차, 골든크로스 예시 |
| `docs/NODES_4_6_7.md` | 4·6·7단계 노드의 원문 설명 |
| `docs/ARCHITECTURE.md` | 모듈 구성과 설계 결정 기록 |
| `README.md` | 실행 방법, 예제 코드, 데이터 파일 |

---

## 1. 큰 그림

### 1.1 전략 = 노드를 선으로 이은 그래프

사용자는 캔버스에 노드를 놓고 선으로 잇는다. 노드 하나는 **역할 하나**만 맡는다. 역할이 곧 캔버스의 단계(띠)이고, 선은 위 단계에서 아래 단계로만 흐른다.

| 단계 | 역할 | 한 문장으로 | 주고받는 데이터 |
|---|---|---|---|
| 1 종목 고르기 | UNIVERSE | 오늘 볼 종목 목록을 만든다 | → 종목 집합 `universe` |
| 2 지표 계산하기 | INDICATOR | 종목마다 숫자를 잰다 (좋고 나쁨은 모른다) | `universe` → 지표 값 `indicator` |
| 3 평가하기 | SCORE · CONDITION · LOGIC | 숫자를 점수나 참·거짓으로 바꾼다 | `indicator` → 점수 `score` · 조건 `condition` |
| 4 매매 대상 정하기 \| 매매 신호 잡기 | SELECTION \| EVENT | 누구를 들고 갈지, 또는 언제 사고팔지 정한다 | → 선정 종목 `selection` \| 신호 `event` |
| 5 비중 정하기 | WEIGHTING · CONSTRAINT · TARGET | 얼마씩 담을지 정한다 | → 비중 `weight` → 목표 포트폴리오 `target_portfolio` \| 진입·청산 계획 `event_plan` |
| 6 매매 결정하기 | STRATEGY | 현재 보유와 비교해 실제 매매를 정한다 | → 전략 판단 `strategy_result` |
| 7 주문하기 | EXECUTION | 언제·어떻게 체결할지 정하고 원장에 기록한다 | `strategy_result` → 체결 |

전략은 두 갈래가 있고, 같은 띠를 공유한다.

- **상태형(팩터·리밸런싱)**: "주기마다 어떤 종목을 몇 %씩 들고 있어야 하나?" 1 → 2 → 3(점수) → 4 대상 → 5 비중·목표 → 6 리밸런싱 하기 → 7.
- **이벤트형(시그널)**: "무슨 일이 생긴 순간 사고판다." 1 → 2 → 3(조건) → 4 신호 → 5 최대 보유 수로 비중 정하기 → 6 신호와 보유 비교하기 → 7.

### 1.2 시점 규칙 (가장 중요)

- 판단은 **평가일 종가** 기준이다. 그 시각까지 공개된 데이터만 본다.
- 체결은 **다음 거래일 시가**다. 같은 날 종가 체결은 없다.
- 재무 공시는 **업로드일 장 마감**에 공개된 것으로 본다(7장). 체결이 항상 다음 날 시가이므로 업로드 날짜만으로 충분하다.
- 회계 기준일(분기 말)은 시점 판단에 쓰지 않는다. 쓰면 공개 전 실적을 미리 보게 된다.

### 1.3 저장소 지도

| 모듈 | 하는 일 |
|---|---|
| `n01_frames` | 노드 사이를 오가는 데이터 구조(프레임)와 시세 봉 |
| `n02_schema` | 노드 계약 타입(`FunctionSpec`·`PortSpec`·`ParamSpec`), 단계(`STAGES`·`LANES`), 실행 설정(`RUN_CONTEXT`) |
| `n03_market_data` | 일봉·재무 읽기, 거래소 시각, 공시 as-of 조회 |
| `n04`~`n06` | 지표·신호·포트폴리오 계산의 기본 함수 |
| `n07_runtime` | 6단계 판단(`evaluate_state`·`evaluate_events`), 주문 수량, 다음 시가 체결 |
| `n08_blocks` | 노드별 계산 함수(핸들러가 부른다) |
| `n09_backtest` | 원장(`Ledger`), 매매 내역 표(`trade_rows`) |
| `n10_functions` | **노드 카탈로그**: 38개 노드의 계약 |
| `n11_registry` | **레지스트리**: 노드 id → 핸들러 |
| `n12_strategies` | 프리셋(자주 쓰는 하위 그래프)과 예제 전략 |
| `n13_connect` | 연결 규칙과 그래프 검증 |
| `n14_engine` | 그래프 실행기 |
| `n15_ohlc_backtest` | 거래일 루프 백테스트 |
| `n16_self_check` | 카탈로그·레지스트리·예제 정합성 검사 |
| `n17_editor_api` | 편집기용 검증·백테스트 진입점 |
| `web/` | React 편집기 (카탈로그 JSON으로 화면을 그린다) |

---

## 2. 노드의 세 층: 정의 · 인스턴스 · 핸들러

노드 하나는 세 층으로 이루어진다. 이 구분이 이 구조의 핵심이다.

| 층 | 무엇 | 어디에 | 누가 쓰나 |
|---|---|---|---|
| **정의 (FunctionSpec)** | 노드 종류의 계약. 이름, 입력·출력 포트, 파라미터, 역할 | `n10_functions.FUNCTIONS` | 편집기(팔레트·검증), 실행기 |
| **인스턴스 (그래프 노드)** | 캔버스에 놓인 노드 하나. 정의를 가리키는 id와 파라미터 값 | 사용자가 저장한 전략 JSON | 저장·불러오기, 실행 |
| **핸들러 (레지스트리)** | 정의를 실제로 계산하는 함수 | `n11_registry` | 실행기 |

같은 정의로 인스턴스를 여러 개 만들 수 있다. 예: 「이동평균 구하기」 정의 하나로 20일선 인스턴스와 60일선 인스턴스를 만든다.

### 2.1 정의: FunctionSpec

```python
FunctionSpec(
    id="condition.compare",            # 영구 id. 저장된 전략이 이 값을 가리키므로 바꾸지 않는다
    name="두 지표 비교하기",             # 화면 이름 ("○○하기")
    category="condition", category_label="조건",
    summary="두 지표를 비교해 참·거짓을 낸다. ...",
    inputs=(PortSpec("left", "왼쪽 지표", "indicator", "비교의 왼쪽.", True),
            PortSpec("right", "오른쪽 지표", "indicator", "비교의 오른쪽. ...", True)),
    outputs=(PortSpec("condition", "조건", "condition", "비교를 만족하면 참.", True),),
    params=(ParamSpec("operator", "비교", "enum", "...", "gt", options=(...)),
            ParamSpec("offsetPct", "여유", "number", "...", 0, unit="%")),
    role="CONDITION",                  # 역할 → 단계(띠)
)
```

| 필드 | 뜻 |
|---|---|
| `id` | 영구 식별자. `단계분류.이름` 형식 |
| `role` | 12개 역할 중 하나. `STAGES`가 역할을 단계로 묶는다 |
| `inputs` / `outputs` | 포트 목록(아래 PortSpec) |
| `params` | 파라미터 목록(아래 ParamSpec) |
| `strategy_execution_type` | 6단계 노드만. `STATE_REBALANCE` 또는 `EVENT_LIFECYCLE` |
| `required_data` | 필요한 데이터 필드. 예: `("close",)`, `("close", "fundamental")`. 실행기는 `fundamental`이 있으면 재무 데이터를 읽는다 |
| `needs_previous` | 전 거래일 입력도 필요한지(예: 진입·청산 시점 잡기). 실행기가 앞쪽 그래프를 전날 기준으로 한 번 더 계산한다 |
| `lookback` | 파라미터로 정해지는 최소 봉 수. 예: 이동평균은 `windowDays` |
| `version` | 계약 버전. 의미가 바뀌면 올린다 |

**PortSpec** (입력·출력 포트)

| 필드 | 뜻 |
|---|---|
| `key`, `label`, `type` | 포트 이름과 대표 타입 |
| `required` | 필수 입력 여부 |
| `accepts` | 여러 타입을 받는 입력. 예: 진입 게이트는 `selection` 또는 `condition` |
| `multiple`, `min_connections` | 선을 여러 개 받는 입력. 예: 조건 묶기는 2개 이상 |
| `same_type_as`, `mirrors` | 입력끼리 타입을 맞추거나, 출력 타입이 입력을 따라가는 경우(조건에 따라 나누기) |
| `auxiliary` | 보조 입력. 단계를 건너뛰어 받을 수 있다(1.1의 선 규칙 예외). 화면에 점선으로 그린다 |

**ParamSpec** (파라미터)

| 필드 | 뜻 |
|---|---|
| `key`, `label`, `type` | `integer` · `number` · `boolean` · `enum` · `date` |
| `default` | 기본값. 인스턴스에 값이 없으면 이 값을 쓴다 |
| `options` | `enum`의 선택지(값, 화면 이름) |
| `unit`, `min`, `max` | 화면 단위와 허용 범위 |

포트 타입은 10개다.

| 타입 | 화면 이름 | 프레임(`n01_frames`) | 행 하나 |
|---|---|---|---|
| `universe` | 종목 집합 | `UniverseFrame(as_of, symbols)` | 종목 id |
| `indicator` | 지표 값 | `IndicatorFrame` | `instrument_id, value` |
| `score` | 종목 점수 | `ScoreFrame` | `instrument_id, score, raw_value, rank` |
| `condition` | 상태 조건 | `ConditionFrame` | `instrument_id, matched` (거짓도 행으로 남는다) |
| `event` | 발생 시점 이벤트 | `EventFrame` | `instrument_id, as_of, kind(entry·exit)` (발생한 것만) |
| `selection` | 선정 종목 | `SelectionFrame` | `instrument_id, rank, score` |
| `weight` | 배분 비중 | `WeightFrame` | `instrument_id, weight` (합 ≤ 1) |
| `target_portfolio` | 목표 포트폴리오 | `TargetPortfolio(positions, cash_weight)` | 목표 비중 + 현금 = 1 |
| `event_plan` | 진입·청산 계획 | `EventPlan(entries, exits, max_positions, eligible, priority)` | — |
| `strategy_result` | 전략 판단 결과 | `StrategyResult(as_of, strategy_id, type, universe, signals, target_weights)` | `AssetSignal(symbol, action, target_weight, reason_codes, ...)` |

`condition`과 `event`의 차이: 조건은 "지금 참인가"라는 **지속 상태**이고, 이벤트는 "지금 바뀌었나"라는 **순간**이다. 이벤트가 없다고 청산하지 않는다. 그날은 `HOLD`다.

### 2.2 편집기용 카탈로그 JSON

`function_catalog()`(`python -m ai_studio.n10_functions`, 편집기는 `npm run catalog`)가 정의를 JSON으로 내보낸다. 편집기는 이 JSON만 보고 팔레트, 띠, 파라미터 입력란, 포트를 그린다. **노드를 추가해도 화면 코드는 고치지 않는다.**

```json
{
  "id": "condition.compare", "name": "두 지표 비교하기", "role": "CONDITION", "stage": "judge",
  "summary": "두 지표를 비교해 참·거짓을 낸다. ...", "needsPrevious": false, "minimumHistory": 0,
  "inputs": [
    {"key": "left", "label": "왼쪽 지표", "type": "indicator", "typeLabel": "지표 값", "required": true,
     "multiple": false, "accepts": [], "auxiliary": false, "...": "..."},
    {"key": "right", "label": "오른쪽 지표", "type": "indicator", "...": "..."}
  ],
  "outputs": [{"key": "condition", "label": "조건", "type": "condition", "...": "..."}],
  "params": [
    {"key": "operator", "label": "비교", "type": "enum", "default": "gt",
     "options": [{"value": "lt", "label": "미만"}, {"value": "lte", "label": "이하"},
                 {"value": "gt", "label": "초과"}, {"value": "gte", "label": "이상"}]},
    {"key": "offsetPct", "label": "여유", "type": "number", "default": 0, "unit": "%"}
  ],
  "instance": {"id": "condition_compare", "function_id": "condition.compare",
               "params": {"operator": "gt", "offsetPct": 0}}
}
```

카탈로그의 최상위에는 `stages`(띠 7개, 4단계의 `lanes` 두 칸 포함), `functions`, `presets`, `examples`, `runContext`(백테스트 실행 설정)가 있다.

### 2.3 인스턴스와 전략 JSON

저장되는 전략은 노드 인스턴스 목록과 선 목록이다. 파라미터는 기본값과 다른 것만 적어도 된다.

```json
{
  "nodes": [
    {"id": "cross_fast", "function_id": "indicator.moving-average", "params": {"windowDays": 20}},
    {"id": "cross_slow", "function_id": "indicator.moving-average", "params": {"windowDays": 60}},
    {"id": "cross_cmp",  "function_id": "condition.compare",        "params": {"operator": "gt"}}
  ],
  "edges": [
    {"from": "cross_fast", "fromPort": "value", "to": "cross_cmp", "toPort": "left"},
    {"from": "cross_slow", "fromPort": "value", "to": "cross_cmp", "toPort": "right"}
  ]
}
```

- `id`: 그래프 안에서만 고유한 인스턴스 이름
- `function_id`: 정의의 `id`
- `params`: 인스턴스의 파라미터 값. 정의에 없는 키는 검증에서 거절된다
- 화면 좌표는 계약에 넣지 않았다. 서비스에서 좌표를 저장하려면 별도 필드(예: `layout`)로 둔다

### 2.4 핸들러와 레지스트리

핸들러는 모두 같은 모양이다.

```python
Handler = Callable[[Inputs, Params, ExecutionContext], dict[str, object]]
#   inputs: 입력 포트 키 → 앞 노드의 출력 프레임 (multiple 포트는 튜플)
#   params: 기본값이 채워진 파라미터
#   반환:   출력 포트 키 → 프레임. 정의의 outputs와 키가 정확히 같아야 한다
```

```python
def _compare(inputs, params, context):
    left = _expect(inputs, "left", IndicatorFrame)
    right = _expect(inputs, "right", IndicatorFrame)
    return {"condition": compare_indicators(left, right, str(params["operator"]), float(params["offsetPct"]))}

register("condition.compare", _compare)   # 정의에 없는 id는 등록 단계에서 거절된다
```

`ExecutionContext`는 블록 출력이 아닌 바깥 정보다. 모든 핸들러가 같은 컨텍스트를 본다.

| 필드 | 내용 |
|---|---|
| `as_of` | 평가 시각(거래소 시각, 타임존 필수) |
| `history` | 종목 → 일봉 목록. 핸들러는 `bars_until(history, symbol, as_of)`로 그 시각까지 공개된 봉만 읽는다 |
| `positions` | 현재 보유(수량, 평균진입가, 진입 시각). 손절·보유기간 노드와 6단계가 쓴다 |
| `previous_inputs` | `needs_previous` 노드에만 채워지는 전 거래일 입력 |
| `fundamentals` | 재무 공시. 재무 노드가 있는 그래프에만 채워진다 |

핸들러 안에서 데이터 소스(DB, 파일)를 직접 읽지 않는다. 데이터는 실행기가 컨텍스트로 넣어 준다. 그래야 같은 그래프를 백테스트와 실거래에서 똑같이 돌릴 수 있다.

### 2.5 실행기

`n14_engine.execute(graph, context)`는 아래 순서로 동작한다.

1. `validate_graph`로 연결 규칙을 검사한다.
2. 위상 정렬 순서로 노드를 하나씩 실행한다. 노드마다 파라미터 기본값을 채우고, 입력 포트에 앞 노드 출력을 모은다.
3. `needs_previous` 노드는 전 거래일 기준으로 앞쪽 그래프를 다시 계산해 `previous_inputs`로 넘긴다.
4. 핸들러를 부르고, 출력 키가 정의와 같은지 확인한다.
5. 데이터가 모자라 빈 결과를 낸 노드는 오류 대신 경고로 남긴다.

실행기에는 전략별 코드가 없다. 새 전략은 노드를 다르게 잇는 것만으로 만들어진다.

### 2.6 연결 규칙과 검증

검증의 최종 판단은 Python 한 곳(`n13_connect`)이 한다. 편집기도 `/api/validate`로 같은 함수를 부른다.

| 규칙 | 내용 |
|---|---|
| 타입 | 출력 타입이 입력의 `type` 또는 `accepts`에 있어야 한다 |
| 단계 순서 | 선은 뒤 단계에서 앞 단계로 갈 수 없다. 주 입력은 같은 단계나 바로 앞 단계에서만 받는다. 보조 입력만 단계를 건너뛴다 |
| 필수·개수 | 필수 입력은 연결되어야 하고, `multiple` 포트는 `min_connections` 이상 |
| 순환 금지 | 그래프에 순환이 없어야 한다 |
| 파라미터 | 정의에 없는 키, 타입·범위·선택지 위반 거절 |
| 완성 | 실행 노드(7단계)가 정확히 하나. 실행 노드에 닿지 않는 노드는 경고 |

`check_editor_graph`의 결과는 `ok`(연결 규칙 통과), `complete`(백테스트 가능한 완성 그래프), `reason`·`completeReason`, `warnings`다. 편집기는 이것으로 「연결 오류 / 완성 전 / 실행 가능」을 표시한다.

---

## 3. 노드 목록과 직관적 의미 (38개)

| 단계 | 노드 (id) | 직관적 의미 |
|---|---|---|
| 1 | 종목 불러오기 (`universe.compose`) | 시장·지수의 전체 종목. 그날 이미 상장한 종목만 |
| 1 | 투자 제외하기 (`universe.exclude`) | 상장 초기·거래정지·거래대금 적은 종목을 뺀다 |
| 1 | 재무 조건으로 거르기 (`universe.fundamental`) | "ROE 20% 이상인 회사만" 같은 재무 필터 |
| 1 | 시가총액으로 거르기 (`universe.market-cap`) | 시가총액 하한, 또는 큰 회사 상위 N개만 |
| 1 | 저가주 빼기 (`universe.min-price`) | 주가가 너무 낮은 종목을 뺀다 |
| 2 | 수익률 구하기 (`indicator.return`) | 지난 N일 동안 얼마나 올랐나 (모멘텀 재료) |
| 2 | 이동평균 구하기 (`indicator.moving-average`) | 최근 N일 평균 가격 (단순·지수) |
| 2 | 가격 가져오기 (`indicator.price`) | 그날 시가·고가·저가·종가 |
| 2 | 기간 고점 구하기 (`indicator.high`) | 어제까지 N일 중 가장 높았던 가격 (돌파 판단용) |
| 2 | 변동성 구하기 (`indicator.volatility`) | 얼마나 출렁이나 (표준편차·베타) |
| 2 | RSI 구하기 (`indicator.rsi`) | 최근 상승·하락 힘의 비율. 과매수·과매도 |
| 2 | MACD 구하기 (`indicator.macd`) | 단기·장기 추세의 차이와 그 평균 |
| 2 | 볼린저 밴드 구하기 (`indicator.bollinger`) | 평균 ± 표준편차 밴드. 밴드 안 위치(%B), 폭 |
| 2 | ATR 구하기 (`indicator.atr`) | 하루 평균 가격 변동폭 |
| 2 | 재무 지표 가져오기 (`indicator.fundamental`) | 그날까지 공시된 PER·PBR·ROE·성장률 등 |
| 3 | 점수 매기기 (`score.from-indicator`) | 지표를 "높을수록 좋다" 점수로 (그대로·순위·표준화) |
| 3 | 점수 합치기 (`score.combine`) | 두 점수를 비율로 섞는다 (화면에서 입력을 감싸는 틀) |
| 3 | 기준값과 비교하기 (`condition.threshold`) | "RSI < 30인가?" |
| 3 | 두 지표 비교하기 (`condition.compare`) | "20일선 > 60일선인가?" |
| 3 | 조건 묶기 (`logic.combine`) | 여러 조건을 AND·OR로 |
| 3 | 조건 뒤집기 (`logic.not`) | 참·거짓을 바꾼다 |
| 3 | 조건에 따라 나누기 (`logic.if-else`) | 조건이 참이면 이 점수, 거짓이면 저 점수 |
| 4 대상 | 상위 N개 고르기 (`selection.top-n`) | 점수 1등부터 N개. 대상 조건, 보유 유지 버퍼 |
| 4 대상 | 점수 기준으로 고르기 (`selection.threshold`) | 점수가 기준을 넘는 종목 전부 |
| 4 대상 | 조건 맞는 종목 고르기 (`selection.condition`) | 조건이 참인 종목 전부 |
| 4 신호 | 진입·청산 시점 잡기 (`event.transition`) | 조건이 거짓→참이면 진입, 참→거짓이면 청산 신호 |
| 4 신호 | 손절·익절 시점 잡기 (`event.stop`) | 보유 종목이 X% 빠지거나 오르면 청산 신호 |
| 4 신호 | 보유기간 만료 시점 잡기 (`event.holding-period`) | 보유 N일이 지나면 청산 신호 |
| 5 | 비중 나누기 (`allocation.target-weight`) | 고른 종목을 똑같이, 또는 점수 비례로 나눈다 |
| 5 | 지표로 비중 나누기 (`allocation.indicator-weight`) | 지표에 비례·반비례로 나눈다 (역변동성 등) |
| 5 | 최대 보유 수로 비중 정하기 (`allocation.event-slots`) | 신호형: 자리 N개, 한 자리 1/N |
| 5 | 비중 상한 두기 (`portfolio.max-weight`) | 한 종목이 X%를 넘지 않게 |
| 5 | 현금 남기기 (`portfolio.cash`) | 전체의 X%는 현금으로 |
| 5 | 국면 따라 줄이기 (`portfolio.risk-off`) | 시장이 나쁘면(조건 거짓) 비중을 줄여 현금으로 |
| 5 | 목표 포트폴리오 만들기 (`portfolio.target`) | 비중을 "목표 비중 + 현금 = 100%"로 확정 |
| 6 | 리밸런싱 하기 (`strategy.state`) | 목표와 보유를 비교해 사고팔 것을 정한다 |
| 6 | 신호와 보유 비교하기 (`strategy.event`) | 신호와 보유·빈 자리를 비교해 사고팔 것을 정한다 |
| 7 | 주기·체결 방식 정하기 (`execution.orders`) | 언제(주기·날짜) 어떻게(무거래 구간·소수 주문·현금 부족) 체결할지 |

4·6·7단계는 `docs/NODES_4_6_7.md`에 원문과 함께 자세히 적었다.

여러 노드를 묶은 자주 쓰는 조합은 노드가 아니라 **프리셋**(저장된 하위 그래프)이다. 예: 「골든크로스·데드크로스」 프리셋 = 이동평균 2개 + 두 지표 비교하기 + 진입·청산 시점 잡기. 프리셋을 놓으면 노드들이 펼쳐져 캔버스에 들어간다.

---

## 4. 백테스트 실행

`n15_ohlc_backtest.run_graph_backtest(graph, history, settings, fundamentals=None)`

1. 그래프를 검증하고, 재무 노드가 있으면 재무 공시를 읽는다.
2. 데이터의 거래일 중 기간 안의 날을 고르고, 신호 확인 주기(`signalFrequency`: 일·주·월)로 **평가일**을 정한다.
3. 7단계 노드의 리밸런스 주기와 날짜로 **리밸런스일**을 정한다. 첫 평가일은 항상 리밸런스일이다.
4. 거래일마다 다음을 반복한다.
   - 그날 종가로 보유를 평가한다.
   - 평가일이면 현재 보유를 컨텍스트에 넣어 그래프를 실행하고, 6단계 판단(`StrategyResult`)을 받는다.
   - 원장(`Ledger.step`)이 주문 수량을 정하고, 다음 거래일 시가에 체결한다. 상태형은 리밸런스일(또는 이탈 허용 초과)에만 주문하고, 이벤트형은 신호가 난 날 주문한다.
5. 결과는 신호 이력, 매매 내역, 보유 이력, 평가액 이력, 미체결 주문이다.

실행 설정(`RUN_CONTEXT`):

| 키 | 화면 이름 | 기본값 |
|---|---|---|
| `startDate`, `endDate` | 조회 시작·종료일 | 비우면 데이터 전체 |
| `signalFrequency` | 신호 확인 | monthly (daily·weekly·monthly) |
| `initialCapital` | 초기 자금 | 10,000 |
| `currency` | 통화 | USD |
| `feeRate`, `slippageRate` | 수수료, 슬리피지 | 0.1%, 0.05% |

주문 수량과 체결가:

- 목표 수량 = 내림(평가금액 × 목표 비중 ÷ 신호일 종가)
- 매도 주문을 먼저 넣고, 매수는 매도 대금을 포함한 남은 현금으로 산다
- 매수 체결가 = 다음 거래일 시가 × (1 + 슬리피지), 매도 체결가 = 시가 × (1 − 슬리피지)
- 수수료 = 수량 × 체결가 × 수수료율
- 실현손익 = (매도 체결가 − 평균 매수 체결가) × 수량 − 매도 수수료

매매 내역 열(`TRADE_COLUMNS`, 편집기 표·CSV 공통): `signal_date, filled_date, symbol, side, quantity, open, high, low, close, fill_price, buy_amount, sell_amount, fee, slippage, realized_pnl, cash_after`

편집기 API(개발 서버 `web/vite.config.ts`가 `n17_editor_api`를 부른다):

| 경로 | 요청 | 응답 |
|---|---|---|
| `POST /api/validate` | 전략 JSON | `{ok, reason, complete, completeReason, warnings}` |
| `POST /api/backtest` | `{graph, settings}` | `{ok, summary, equity, tradeColumns, trades, warnings}` 또는 `{ok: false, reason}` |

참조 구현은 요청마다 CSV를 다시 읽어 3초 정도 걸린다. 서비스에서는 백엔드 API, 시세 캐시, 비동기 실행이 필요하다.

---

## 5. 데이터와 조인

### 5.1 일봉 (OHLCV)

`data/sp500_ohlc_1y.csv`: `date, symbol, open, high, low, close, volume`

읽을 때 봉마다 시각을 붙인다(`n03.daily_bar`).

- `open_at` = 그날 09:30 (America/New_York)
- `timestamp` = `available_at` = 그날 16:00 (장 마감)

미국 동부 시각으로 계산해야 서머타임에 한 시간씩 어긋나지 않는다. 휴장일 달력은 없고, 데이터에 있는 날을 거래일로 본다.

### 5.2 재무 (long 형식)

`data/sp500_fundamentals_sample.csv`(**예시값**, `scripts/make_sample_fundamentals.py`가 만든다)

| symbol | account_code | value | unit | fiscal_period | fiscal_date | upload_date |
|---|---|---|---|---|---|---|
| AAPL | ROE | 25.85 | % | 2025Q3 | 2025-09-30 | 2025-11-14 |

- 한 행 = 종목 × 계정 × 분기
- `fiscal_date`: 공시가 적용되는 회계 기준일(분기 말)
- `upload_date`: 공시가 실제로 올라온 날(**미국 동부 날짜**). 이날 장 마감부터 쓸 수 있다
- 계정코드 11개: REVENUE, NET_INCOME, TOTAL_ASSETS, TOTAL_LIABILITIES, TOTAL_EQUITY, SHARES_OUTSTANDING(백만 단위), EPS, BPS(달러), ROE, ROA, DEBT_RATIO(퍼센트 값, 10%는 10.0)
- 같은 분기가 정정되면 같은 `fiscal_period`로 새 `upload_date` 행을 추가한다. 정정 전 기간에는 원래 값이, 정정일부터는 정정값이 쓰인다

가격과 섞이는 값(PER·PBR·PSR·시가총액)은 테이블에 넣지 않고 조인한 뒤 그날 종가로 계산한다. 매일 바뀌는 값이기 때문이다.

### 5.3 조인 원칙: 업로드일 기준 as-of

각 거래일에 "그날까지 올라온 가장 최근 공시"를 붙인다. 조건은 `upload_date <= date`이고, 그중 최신 한 건만 남긴다. `fiscal_date`로 붙이면 미래 참조가 된다.

예: AAPL은 2025-11-13까지 2025Q2 값, 2025Q3이 올라온 2025-11-14부터 2025Q3 값이 붙는다.

**DB에서 권장하는 방식: 유효 구간 테이블**

공시를 적재할 때 유효 구간을 미리 만들어 둔다. 조회할 때마다 `LEAD`를 계산하지 않는다.

```sql
CREATE TABLE fundamental_filing (
  symbol        varchar  NOT NULL,
  account_code  varchar  NOT NULL,
  fiscal_period varchar  NOT NULL,      -- '2025Q3'
  fiscal_date   date     NOT NULL,
  value         numeric  NOT NULL,
  unit          varchar  NOT NULL,
  valid_from    date     NOT NULL,      -- = upload_date (미국 동부 날짜)
  valid_to      date     NOT NULL,      -- 다음 공시의 upload_date, 없으면 '9999-12-31'
  PRIMARY KEY (symbol, account_code, valid_from)
);
-- 새 공시를 넣을 때 같은 (symbol, account_code)의 직전 행 valid_to를 새 valid_from으로 닫는다.
```

```sql
-- 거래일과 붙이기 (날짜당 정확히 한 행)
SELECT o.date, o.symbol, o.close, f.fiscal_period, f.value AS roe
FROM ohlcv o
JOIN fundamental_filing f
  ON f.symbol = o.symbol AND f.account_code = 'ROE'
 AND o.date >= f.valid_from AND o.date < f.valid_to;
```

as-of 조인 문법이 있는 DB(DuckDB·Snowflake·ClickHouse의 `ASOF JOIN`)나 pandas `merge_asof(direction="backward")`도 같은 결과를 낸다.

**백테스트에서 권장하는 방식: DB에서는 범위만 읽고, 맞추기는 메모리에서**

재무는 일봉보다 훨씬 작다(503종목 × 11계정 × 2년이면 약 5만 행). 백테스트는 평가일에만 재무를 본다. 그래서 일봉 전체와 DB 안에서 조인하기보다, 필요한 범위를 한 번 읽고 엔진 안에서 맞추는 편이 가장 가볍다. 참조 구현이 이 방식이다.

```sql
SELECT symbol, account_code, fiscal_period, value, valid_from AS upload_date
FROM fundamental_filing
WHERE symbol = ANY(:symbols) AND account_code = ANY(:accounts)
  AND valid_from <= :end_date;   -- 시작일 직전 공시도 필요하므로 시작일 조건은 걸지 않는다
```

엔진(`n03`)은 읽은 공시를 종목·계정별로 공개시각 순으로 정렬해 두고, 평가일마다 이진 탐색으로 찾는다.

| 함수 | 내용 |
|---|---|
| `load_fundamentals()` | long 표 → `{종목: {계정: (Filing(available_at, fiscal_period, value), ...)}}`. `available_at` = 업로드일 16:00 동부 |
| `filing_as_of(f, symbol, account, as_of)` | as_of까지 공개된 최신 공시 |
| `trailing_sum(f, symbol, account, as_of, 4)` | 최근 4분기 합(정정은 나중 값). PER·PSR에 쓴다 |

재무 노드 계산식:

| 항목 | 계산 |
|---|---|
| PER | 그날 종가 ÷ 최근 4분기 EPS 합 (합이 0 이하면 값 없음) |
| PBR | 그날 종가 ÷ 최근 BPS |
| PSR | 그날 종가 ÷ (최근 4분기 매출 합 ÷ 최근 발행주식수) |
| 시가총액 | 그날 종가 × 최근 발행주식수 (십억 달러) |
| 매출·순이익·EPS 성장률 | 최근 분기 ÷ 전년 같은 분기 − 1 (%). 전년이 음수면 \|전년\|으로 나눈다 |
| 그 밖의 계정 | 최근 공시값 |

**데이터 쪽에서 합의가 필요한 것**

- 업로드일은 거래소 시간대(미국은 동부) 날짜로 적재한다. 한국 시각으로 적으면 미국 장 마감 뒤 공시가 하루 늦게 잡힌다.
- 당일 종가 체결을 추가한다면 업로드 **시각**이 필요하다. 지금은 체결이 항상 다음 날 시가라 날짜로 충분하다.
- 일봉도 같은 원칙이다. `available_at`은 실제로 그 값을 받을 수 있게 된 시각이어야 한다.

---

## 6. 새 노드 추가 절차

MACD 노드(`indicator.macd`)를 예로 든다. 화면 코드는 고치지 않는다.

1. **계산 함수** (`n04`·`n08`): 종목별 값을 내는 순수 함수를 만든다. `as_of`까지 공개된 봉만 쓴다(`_upto`/`bars_until`).
   ```python
   def macd_indicator(history, symbols, as_of, *, fast_days=12, slow_days=26, signal_days=9,
                      line="macd", scope="symbol") -> IndicatorFrame: ...
   ```
2. **정의** (`n10_functions`): `FunctionSpec`을 추가한다. 역할, 포트, 파라미터(화면 이름·설명·단위·기본값), 최소 봉 수(`lookback`).
   - 값이 여러 줄인 지표는 "어느 줄을 낼지"를 파라미터로 받아 **출력 포트를 하나로** 유지한다. 그래야 기존 비교·점수 노드에 그대로 이어진다.
   - 하는 일이 기존 노드와 같으면 새 노드 대신 기존 노드의 옵션으로 넣는다(예: EMA는 이동평균 구하기의 평균 방식).
3. **핸들러 등록** (`n11_registry`): 파라미터 키를 함수 인자로 옮기는 핸들러를 `register_builtin_handlers`에 넣는다.
4. **테스트** (`tests/`): 손으로 계산할 수 있는 작은 입력으로 값을 고정하고, 데이터가 모자랄 때의 동작도 확인한다.
5. **정합성 검사**: `python -m ai_studio.n16_self_check`. 모든 정의에 핸들러가 있는지, 프리셋·예제가 검증을 통과하는지 확인한다.
6. **카탈로그 갱신**: `cd web && npm run catalog`. 편집기 팔레트에 새 노드가 나타난다.

기존 노드의 의미를 바꿀 때는 `version`을 올리고, 저장된 전략의 변환 규칙을 함께 정한다.

---

## 7. 예시 워크플로우: 골든크로스

예제 「골든크로스 / 데드크로스」(`EXAMPLES["golden-cross"]`)를 처음부터 끝까지 글로 따라간다. 숫자는 실제 1년치 S&P 500 일봉, 매일 평가, 초기 자금 10만 달러, 수수료 0.1%, 슬리피지 0.05%로 돌린 결과다.

### 7.1 캔버스

```
1 종목 고르기   종목 불러오기(S&P 500) → 투자 제외하기(상장 60일 이상)
2 지표 계산하기 이동평균 구하기(20일) | 이동평균 구하기(60일)
3 평가하기     두 지표 비교하기(20일선 > 60일선)
4 매매 신호 잡기 진입·청산 시점 잡기
5 비중 정하기   최대 보유 수로 비중 정하기(10종목)
6 매매 결정하기 신호와 보유 비교하기
7 주문하기     주기·체결 방식 정하기(기본값)
```

전략 JSON(노드 9개, 선 10개):

```json
{"nodes": [
  {"id": "universe",   "function_id": "universe.compose",         "params": {"market": "US_EQUITY", "benchmark": "SPX"}},
  {"id": "filter",     "function_id": "universe.exclude",         "params": {"minListingDays": 60}},
  {"id": "cross_fast", "function_id": "indicator.moving-average", "params": {"windowDays": 20}},
  {"id": "cross_slow", "function_id": "indicator.moving-average", "params": {"windowDays": 60}},
  {"id": "cross_cmp",  "function_id": "condition.compare",        "params": {"operator": "gt"}},
  {"id": "cross_edge", "function_id": "event.transition",         "params": {}},
  {"id": "slots",      "function_id": "allocation.event-slots",   "params": {"maxPositions": 10}},
  {"id": "decision",   "function_id": "strategy.event",           "params": {}},
  {"id": "orders",     "function_id": "execution.orders",         "params": {}}
 ],
 "edges": [
  {"from": "universe",   "fromPort": "universe",  "to": "filter",     "toPort": "universe"},
  {"from": "filter",     "fromPort": "universe",  "to": "cross_fast", "toPort": "universe"},
  {"from": "filter",     "fromPort": "universe",  "to": "cross_slow", "toPort": "universe"},
  {"from": "cross_fast", "fromPort": "value",     "to": "cross_cmp",  "toPort": "left"},
  {"from": "cross_slow", "fromPort": "value",     "to": "cross_cmp",  "toPort": "right"},
  {"from": "cross_cmp",  "fromPort": "condition", "to": "cross_edge", "toPort": "condition"},
  {"from": "cross_edge", "fromPort": "entry",     "to": "slots",      "toPort": "entry"},
  {"from": "cross_edge", "fromPort": "exit",      "to": "slots",      "toPort": "exit"},
  {"from": "slots",      "fromPort": "plan",      "to": "decision",   "toPort": "plan"},
  {"from": "decision",   "fromPort": "result",    "to": "orders",     "toPort": "result"}
 ]}
```

캔버스에서는 「골든크로스·데드크로스」 프리셋을 놓으면 2~4단계 노드 4개가 한 번에 들어간다.

### 7.2 평가일 하루에 일어나는 일 (2025-12-24, BR 종목)

**1단계 · 종목 고르기.** 「종목 불러오기」가 그날까지 봉이 있는 S&P 500 종목 목록을 낸다. 「투자 제외하기」가 상장 60거래일 미만, 그날 봉이 없는 종목을 뺀다. 데이터가 2025-09-30부터라 61번째 거래일인 12월 24일 무렵부터 종목이 남는다. 결과는 `universe`(종목 집합)다.

**2단계 · 지표 계산하기.** 같은 종목 집합을 받아 이동평균 노드 두 개가 각각 계산한다. BR의 12월 24일 종가 기준 20일선은 228.33, 60일선은 228.20이다. 이 단계는 좋고 나쁨을 판단하지 않고 숫자만 낸다(`indicator`).

**3단계 · 평가하기.** 「두 지표 비교하기」(초과)가 종목마다 "20일선 > 60일선"이 참인지 낸다. BR은 228.33 > 228.20이라 참이다. 조건은 거짓인 종목도 행으로 남긴다(`condition`).

**4단계 · 매매 신호 잡기.** 「진입·청산 시점 잡기」는 전 거래일 조건도 필요한 노드다. 실행기가 2~3단계를 12월 23일 기준으로 한 번 더 계산해 넘긴다. 23일 BR은 20일선 228.32 < 60일선 228.37로 거짓이었다. 거짓 → 참으로 바뀌었으므로 **진입 신호**가 나온다. 이 노드는 보유나 현금은 보지 않는다(`event`).

**5단계 · 최대 보유 수로 비중 정하기.** 그날 나온 진입 신호(BR, BX, ESS, HPE, KMI, PLTR, RJF, TEL 등)와 청산 신호를 모아 "최대 10종목, 한 종목 1/10"이라는 계획을 만든다(`event_plan`).

**6단계 · 신호와 보유 비교하기.** 현재 보유와 비교한다. 첫 진입일이라 보유가 없고 빈 자리가 10개다. 진입 신호 8종목 모두 `ENTER`, 목표 비중 10%가 된다. 보유하지 않은 종목에 온 청산 신호 3건은 `HOLD`로 무시된다(`strategy_result`).

**7단계 · 주기·체결 방식 정하기.** 이벤트형이라 리밸런스 주기와 관계없이 신호가 난 날 주문한다.
- 수량: 내림(평가금액 100,000 × 10% ÷ 12월 24일 종가 228.38) = **43주**
- 체결: 다음 거래일인 12월 26일(25일은 휴장) 시가 228.56 × (1 + 0.05%) = **228.67달러**
- 매수 금액 9,832.99달러, 수수료 9.83달러, 슬리피지 4.91달러, 체결 뒤 현금 90,157.17달러

### 7.3 그다음 날들

- **12월 26일**: AOS, BA, CCL, INTU, MA, V 등에 골든크로스가 난다. 남은 자리는 2개라 AOS, BA만 `ENTER`(12월 29일 시가 체결)이고, 나머지는 `HOLD`(사유 `NO_SLOT`)다. 보유 10종목으로 자리가 다 찬다.
- **2026년 1월 5일**: TEL에 데드크로스(참 → 거짓)가 나서 청산 신호가 나온다. 같은 날 EG에 진입 신호가 있다. 6단계는 청산으로 생긴 빈 자리를 그날 진입에 쓴다. 1월 6일 시가에 TEL을 팔고 EG를 산다.
- **1월 6일**: BR의 20일선 226.61이 60일선 226.92 아래로 내려간다(전날 5일은 둘 다 227.13으로 근소하게 참). 참 → 거짓이므로 청산 신호가 나오고, 1월 7일 시가 222.36 × (1 − 0.05%) = 222.25달러에 43주를 판다. 실현손익은 (222.25 − 228.67) × 43 − 매도 수수료 9.56 = **−285.85달러**다.
- 조건이 계속 참이거나 계속 거짓인 날에는 신호가 없고, 보유는 그대로다.

1년 결과: 체결 98건, 누적 수익률 +10.75%. 편집기에서 「백테스트 하기」를 누르면 같은 98건이 매매 내역 표로 나온다.

### 7.4 이 예시에서 볼 점

- 4단계는 "언제"만, 6단계는 "실제로 살지"를, 7단계는 "어떻게 체결할지"를 정한다. 역할이 겹치지 않는다.
- 판단은 24일 종가, 체결은 26일 시가다. 판단에 쓴 데이터 중 판단 시점 뒤에 공개된 것은 없다.
- 이동평균 기간을 바꾸려면 노드 파라미터만, 손절을 추가하려면 「손절·익절 시점 잡기」를 5단계 청산 입력에 하나 더 이으면 된다. 엔진 코드는 바뀌지 않는다.

---

## 8. 수행사와 정할 것

| 항목 | 현재 참조 구현 | 서비스에서 필요한 것 |
|---|---|---|
| 데이터 소스 | S&P 500 1년 일봉 CSV, 재무 예시값 | 시세·재무 어댑터, 업로드일(시각) 보장, 과거 지수 구성종목, 업종 분류 |
| 시장 현실 | 휴장 달력·분할·배당·세금 없음 | 기업행동 처리, 국내 시장 규칙(거래세·호가 단위) |
| 실행 | 개발 서버에서 요청마다 실행(약 3초) | 백테스트 API, 시세 캐시, 비동기 작업 |
| 저장 | 전략 JSON(노드·선·파라미터) | 전략 저장소, 버전, 화면 좌표 필드 |
| 미구현 옵션 | 투자 제외하기의 금융주 제외·적자 기업 제외는 데이터가 없어 오류를 낸다 | 업종 데이터 연결, 적자 제외는 재무 조건으로 거르기(순이익 > 0)와 통합 검토 |
| 호환성 | 이전 블록 id 일부 삭제 | 저장된 전략 변환 규칙 |

검증 명령:

```bash
python -m ai_studio.n16_self_check       # 카탈로그·레지스트리·프리셋·예제 정합성
python -m unittest discover -s tests     # 단위·통합 테스트 (예제 10개 백테스트 포함)
cd web && npm run catalog && npm run dev # 편집기
```
