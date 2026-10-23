from collections.abc import Callable, Mapping
from dataclasses import replace

from ai_studio.n02_schema import (
    PORT_TYPE_LABEL, FunctionSpec, ParamOption, ParamSpec, PortSpec, RequiredData,
)

_UNIVERSE_OUT = PortSpec(
    "universe",
    "종목 집합",
    "universe",
    "조건을 통과한 종목 목록.",
    required=True,
)
_UNIVERSE_IN = PortSpec(
    "universe",
    "종목 집합",
    "universe",
    "앞 단계에서 넘어온 투자 대상.",
    required=True,
)


def _opts(*pairs: tuple[str, str]) -> tuple[ParamOption, ...]:
    return tuple(ParamOption(value, label) for value, label in pairs)


_LEGACY_FUNCTIONS: tuple[FunctionSpec, ...] = (
    FunctionSpec(
        id="universe.compose",
        name="유니버스 구성",
        category="universe",
        category_label="유니버스",
        summary="시장과 지수, 규모 조건으로 투자 대상 종목 집합을 만든다.",
        inputs=(),
        outputs=(_UNIVERSE_OUT,),
        params=(
            ParamSpec(
                "market",
                "시장",
                "enum",
                "종목을 가져올 시장.",
                default="US_EQUITY",
                options=_opts(
                    ("US_EQUITY", "미국 주식"),
                    ("KR_EQUITY", "국내 주식"),
                    ("ETF", "ETF"),
                ),
            ),
            ParamSpec(
                "benchmark",
                "기준 지수",
                "enum",
                "유니버스의 모집단이 되는 지수. 선택한 시장에 있는 지수만 화면에 보여 준다.",
                default="SPX",
                options=_opts(
                    ("SPX", "S&P 500"),
                    ("NDX", "나스닥 100"),
                    ("KOSPI", "코스피"),
                    ("KOSDAQ", "코스닥"),
                    ("ETF_LISTED", "상장 ETF"),
                ),
            ),
            ParamSpec(
                "minMarketCap",
                "최소 시가총액",
                "number",
                "이 금액보다 작은 종목은 유니버스에서 뺀다. 0이면 제한 없음.",
                default=0,
                min=0,
                unit="억원",
            ),
        ),
    ),
    FunctionSpec(
        id="universe.exclude",
        name="투자 제외 조건",
        category="universe",
        category_label="유니버스",
        summary="종목 집합에서 거래 불가·저유동성·상장 초기 종목을 걸러 낸다.",
        inputs=(_UNIVERSE_IN,),
        outputs=(_UNIVERSE_OUT,),
        params=(
            ParamSpec(
                "excludeFinancials",
                "금융주 제외",
                "boolean",
                "은행·보험·증권 등 금융 업종을 뺀다.",
                default=False,
            ),
            ParamSpec(
                "excludeLossMaking",
                "적자 기업 제외",
                "boolean",
                "최근 결산 순이익이 0 이하인 종목을 뺀다.",
                default=False,
            ),
            ParamSpec(
                "minAverageTurnover",
                "최소 평균 거래대금",
                "number",
                "최근 20거래일 평균 거래대금이 이 값보다 작으면 제외한다.",
                default=0,
                min=0,
                unit="억원",
            ),
            ParamSpec(
                "minListingDays",
                "최소 상장일수",
                "integer",
                "상장 후 이 일수보다 짧은 종목을 제외한다.",
                default=252,
                min=0,
                unit="일",
            ),
            ParamSpec(
                "excludeHalted",
                "거래정지 제외",
                "boolean",
                "신호일 기준 거래정지 종목을 뺀다.",
                default=True,
            ),
        ),
    ),
    FunctionSpec(
        id="universe.min-price",
        name="최소 가격",
        category="universe",
        category_label="유니버스",
        summary="기준일 종가가 하한보다 낮은 종목을 뺀다. 가격 외의 조건은 보지 않는다.",
        inputs=(_UNIVERSE_IN,),
        outputs=(_UNIVERSE_OUT,),
        params=(
            ParamSpec(
                "minClose",
                "최소 종가",
                "number",
                "기준일 종가가 이 값보다 낮으면 제외한다. 0이면 제한 없음.",
                default=0,
                min=0,
                unit="달러",
            ),
        ),
    ),
    FunctionSpec(
        id="momentum.factor",
        name="모멘텀 팩터 점수",
        category="momentum",
        category_label="모멘텀 신호",
        summary="과거 수익률만으로 점수를 준다. 종목 선정과 비중은 정하지 않는다.",
        inputs=(_UNIVERSE_IN,),
        outputs=(
            PortSpec(
                "score",
                "모멘텀 점수",
                "score",
                "종목별 모멘텀 점수. 높을수록 강하다.",
                required=True,
            ),
        ),
        params=(
            ParamSpec(
                "lookbackMonths",
                "측정 기간",
                "integer",
                "수익률을 계산하는 개월 수.",
                default=12,
                min=1,
                max=60,
                unit="개월",
            ),
            ParamSpec(
                "skipMonths",
                "최근 제외",
                "integer",
                "단기 반전을 피하기 위해 최근 개월은 수익률 계산에서 뺀다.",
                default=1,
                min=0,
                max=12,
                unit="개월",
            ),
        ),
    ),
    FunctionSpec(
        id="momentum.trend",
        name="추세 상태 조건",
        category="momentum",
        category_label="모멘텀 조건",
        summary="이동평균 배열이나 가격 위치로 상승 추세 종목을 고른다.",
        inputs=(_UNIVERSE_IN,),
        outputs=(
            PortSpec(
                "condition",
                "추세 조건",
                "condition",
                "상승 추세로 판단된 종목은 참, 나머지는 거짓.",
                required=True,
            ),
        ),
        params=(
            ParamSpec(
                "method",
                "판단 방식",
                "enum",
                "추세를 참·거짓으로 바꾸는 규칙.",
                default="price_above_ma",
                options=_opts(
                    ("price_above_ma", "가격이 장기 이평 위"),
                    ("ma_cross", "단기 이평이 장기 이평 위"),
                ),
            ),
            ParamSpec(
                "fastDays",
                "단기 기간",
                "integer",
                "단기 이동평균 일수. 교차 방식에서 사용한다.",
                default=20,
                min=1,
                unit="일",
            ),
            ParamSpec(
                "slowDays",
                "장기 기간",
                "integer",
                "장기 이동평균 일수.",
                default=60,
                min=2,
                unit="일",
            ),
        ),
    ),
    FunctionSpec(
        id="momentum.breakout",
        name="신고가 돌파 상태 조건",
        category="momentum",
        category_label="모멘텀 조건",
        summary="정해진 기간의 고점을 돌파했는지 종목별 조건을 계산한다.",
        inputs=(_UNIVERSE_IN,),
        outputs=(
            PortSpec(
                "condition",
                "돌파 조건",
                "condition",
                "고점 돌파 조건에 맞는 종목은 참.",
                required=True,
            ),
        ),
        params=(
            ParamSpec(
                "windowDays",
                "고점 기간",
                "integer",
                "이전 고점을 찾는 거래일 수. 252는 약 1년이다.",
                default=252,
                min=2,
                unit="일",
            ),
            ParamSpec(
                "bufferPct",
                "돌파 여유",
                "number",
                "이전 고점보다 이 비율 이상 올라야 돌파로 본다.",
                default=0,
                min=0,
                unit="%",
            ),
        ),
    ),
    FunctionSpec(
        id="allocation.target-weight",
        name="목표 비중 자산배분",
        category="allocation",
        category_label="비중·청산",
        summary="선정된 종목만 동일 비중 또는 점수 비례 비중으로 나눈다. 상한과 현금은 정하지 않는다.",
        inputs=(
            PortSpec(
                "selection",
                "선정 종목",
                "selection",
                "랭킹 단계에서 고른 종목. 점수 비례일 때는 선정 결과에 점수가 있어야 한다.",
                required=True,
            ),
        ),
        outputs=(
            PortSpec(
                "weight",
                "배분 비중",
                "weight",
                "제약 적용 전의 종목 비중. 주문 수량은 포함하지 않는다.",
                required=True,
            ),
        ),
        params=(
            ParamSpec(
                "scheme",
                "배분 방식",
                "enum",
                "종목 사이 비중을 나누는 규칙.",
                default="equal",
                options=_opts(
                    ("equal", "동일 비중"),
                    ("score", "점수 비례"),
                ),
            ),
        ),
    ),
    FunctionSpec(
        id="allocation.stop",
        name="손절 익절 청산",
        category="allocation",
        category_label="비중·청산",
        summary="손실 또는 이익이 기준을 넘은 종목의 목표 비중을 0으로 만든다.",
        inputs=(
            PortSpec(
                "weight",
                "목표 비중",
                "weight",
                "청산 규칙을 적용할 현재 목표 비중.",
                required=True,
            ),
        ),
        outputs=(
            PortSpec(
                "weight",
                "조정된 비중",
                "weight",
                "손절·익절이 반영된 목표 비중.",
                required=True,
            ),
        ),
        params=(
            ParamSpec(
                "stopLossPct",
                "손절",
                "number",
                "진입가 대비 이 비율 이상 하락하면 비중을 0으로 한다.",
                default=10,
                min=0,
                unit="%",
            ),
            ParamSpec(
                "takeProfitPct",
                "익절",
                "number",
                "진입가 대비 이 비율 이상 상승하면 비중을 0으로 한다.",
                default=30,
                min=0,
                unit="%",
            ),
            ParamSpec(
                "trailing",
                "고점 대비 손절",
                "boolean",
                "진입가가 아니라 보유 중 고점 대비 하락으로 손절한다.",
                default=False,
            ),
        ),
    ),
    FunctionSpec(
        id="allocation.rebalance",
        name="정기 조건부 리밸런싱",
        category="allocation",
        category_label="비중·청산",
        summary="정해진 주기이거나, 실제 비중이 목표에서 충분히 벗어났을 때만 다시 맞춘다.",
        inputs=(
            PortSpec(
                "weight",
                "목표 비중",
                "weight",
                "리밸런스 여부를 판단할 목표 비중.",
                required=True,
            ),
        ),
        outputs=(
            PortSpec(
                "weight",
                "리밸런스 비중",
                "weight",
                "이번 시점에 실제로 맞출 목표 비중.",
                required=True,
            ),
        ),
        params=(
            ParamSpec(
                "cadence",
                "정기 주기",
                "enum",
                "이 주기가 되면 비중을 다시 맞춘다.",
                default="monthly",
                options=_opts(
                    ("weekly", "주간"),
                    ("monthly", "월간"),
                    ("quarterly", "분기"),
                    ("semiannual", "반기"),
                ),
            ),
            ParamSpec(
                "driftThresholdPct",
                "이탈 허용",
                "number",
                "실제 비중과 목표 비중 차이가 이 값 이상이면 주기 전에도 맞춘다.",
                default=5,
                min=0,
                unit="%p",
            ),
        ),
    ),
    FunctionSpec(
        id="factor.value",
        name="가치(밸류) 팩터",
        category="factor",
        category_label="팩터 전략",
        summary="저평가일수록 높은 점수를 준다. 상위 종목 선정은 하지 않는다.",
        inputs=(_UNIVERSE_IN,),
        outputs=(
            PortSpec(
                "score",
                "가치 점수",
                "score",
                "종목별 가치 점수. 높을수록 저평가다.",
                required=True,
            ),
        ),
        params=(
            ParamSpec(
                "metric",
                "지표",
                "enum",
                "저평가를 재는 재무 지표.",
                default="composite",
                options=_opts(
                    ("per", "PER"),
                    ("pbr", "PBR"),
                    ("psr", "PSR"),
                    ("composite", "PER·PBR·PSR 결합"),
                ),
            ),
        ),
    ),
    FunctionSpec(
        id="factor.quality",
        name="퀄리티 팩터",
        category="factor",
        category_label="팩터 전략",
        summary="우량할수록 높은 점수를 준다. 상위 종목 선정은 하지 않는다.",
        inputs=(_UNIVERSE_IN,),
        outputs=(
            PortSpec(
                "score",
                "퀄리티 점수",
                "score",
                "종목별 퀄리티 점수. 높을수록 우량하다.",
                required=True,
            ),
        ),
        params=(
            ParamSpec(
                "metric",
                "지표",
                "enum",
                "우량도를 재는 재무 지표.",
                default="composite",
                options=_opts(
                    ("roe", "ROE"),
                    ("roa", "ROA"),
                    ("debt", "부채비율"),
                    ("composite", "ROE·ROA·부채비율 결합"),
                ),
            ),
        ),
    ),
    FunctionSpec(
        id="factor.low-volatility",
        name="저변동성 팩터",
        category="factor",
        category_label="팩터 전략",
        summary="변동이 작을수록 높은 점수를 준다. 하위 종목 선정은 하지 않는다.",
        inputs=(_UNIVERSE_IN,),
        outputs=(
            PortSpec(
                "score",
                "저변동성 점수",
                "score",
                "종목별 저변동성 점수. 높을수록 변동이 작다.",
                required=True,
            ),
        ),
        params=(
            ParamSpec(
                "metric",
                "지표",
                "enum",
                "변동을 재는 방식.",
                default="stdev",
                options=_opts(("stdev", "수익률 표준편차"), ("beta", "시장 베타")),
            ),
            ParamSpec(
                "windowDays",
                "측정 기간",
                "integer",
                "변동을 계산하는 거래일 수.",
                default=252,
                min=20,
                unit="일",
            ),
        ),
    ),
    FunctionSpec(
        id="logic.combine",
        name="복수 조건 조합",
        category="logic",
        category_label="논리",
        summary="여러 상태 조건을 AND 또는 OR로 하나로 합친다.",
        inputs=(
            PortSpec(
                "conditions",
                "조건",
                "condition",
                "합칠 조건. 두 개 이상 연결한다.",
                required=True,
                multiple=True,
                min_connections=2,
            ),
        ),
        outputs=(
            PortSpec(
                "condition",
                "결합 조건",
                "condition",
                "조합 규칙으로 합쳐진 참·거짓.",
                required=True,
            ),
        ),
        params=(
            ParamSpec(
                "operator",
                "조합",
                "enum",
                "모든 조건이 참이어야 하는지, 하나면 충분한지.",
                default="and",
                options=_opts(("and", "모두 참 (AND)"), ("or", "하나라도 참 (OR)")),
            ),
        ),
    ),
    FunctionSpec(
        id="logic.if-else",
        name="IF - ELSE",
        category="logic",
        category_label="논리",
        summary="조건이 참이면 한쪽 결과를, 거짓이면 다른 쪽 결과를 내보낸다.",
        inputs=(
            PortSpec(
                "condition",
                "조건",
                "condition",
                "어느 분기를 쓸지 정하는 참·거짓 조건.",
                required=True,
            ),
            PortSpec(
                "thenBranch",
                "참일 때",
                "score",
                "조건이 참일 때 내보낼 값.",
                required=True,
                accepts=("condition", "score"),
            ),
            PortSpec(
                "elseBranch",
                "거짓일 때",
                "score",
                "조건이 거짓일 때 내보낼 값. 참일 때와 같은 타입이어야 한다.",
                required=True,
                accepts=("condition", "score"),
                same_type_as="thenBranch",
            ),
        ),
        outputs=(
            PortSpec(
                "result",
                "선택 결과",
                "score",
                "조건에 따라 둘 중 하나가 나온 결과. 타입은 두 분기와 같다.",
                required=True,
                accepts=("condition", "score"),
                mirrors="thenBranch",
            ),
        ),
        params=(),
    ),
    FunctionSpec(
        id="logic.set-op",
        name="전략 집합 연산",
        category="logic",
        category_label="논리",
        summary="두 종목 집합 또는 두 조건을 합집합, 교집합, 차집합으로 합친다.",
        inputs=(
            PortSpec(
                "left",
                "왼쪽 집합",
                "universe",
                "연산의 기준 집합.",
                required=True,
                accepts=("universe", "condition"),
            ),
            PortSpec(
                "right",
                "오른쪽 집합",
                "universe",
                "기준 집합과 합칠 집합. 왼쪽과 같은 타입이어야 한다.",
                required=True,
                accepts=("universe", "condition"),
                same_type_as="left",
            ),
        ),
        outputs=(
            PortSpec(
                "result",
                "연산 결과",
                "universe",
                "집합 연산 결과. 타입은 입력과 같다.",
                required=True,
                accepts=("universe", "condition"),
                mirrors="left",
            ),
        ),
        params=(
            ParamSpec(
                "operator",
                "연산",
                "enum",
                "두 집합을 합치는 방식.",
                default="intersect",
                options=_opts(
                    ("union", "합집합"),
                    ("intersect", "교집합"),
                    ("difference", "차집합 (왼쪽 − 오른쪽)"),
                ),
            ),
        ),
    ),
)

# 기존 ID와 포트 키는 유지한다. 점수·상태 조건·비중은 전략 최종 결과가 아닌 중간값이다.
_SCORE_IDS = {
    "momentum.factor", "factor.value", "factor.quality", "factor.low-volatility"
}
_REQUIRED_DATA: dict[str, RequiredData] = {
    "momentum.factor": RequiredData(("close",), 252, "1d"),
    "momentum.trend": RequiredData(("close",), 60, "1d"),
    "momentum.breakout": RequiredData(("high", "close"), 253, "1d"),
    "factor.value": RequiredData(("per", "pbr", "psr", "available_at")),
    "factor.quality": RequiredData(("roe", "roa", "debt_ratio", "available_at")),
    "factor.low-volatility": RequiredData(("close",), 252, "1d"),
}


def _legacy_role(fn: FunctionSpec) -> str:
    if fn.category == "universe":
        return "UNIVERSE"
    if fn.id in _SCORE_IDS:
        return "SCORE"
    if fn.id == "allocation.target-weight":
        return "WEIGHTING"
    if fn.id in {"allocation.stop", "allocation.rebalance"}:
        return "CONSTRAINT"
    if fn.category == "logic":
        return "LOGIC"
    if fn.category == "allocation":
        return "ALLOCATION"
    return "CONDITION"


_SCOPE = ParamSpec(
    "scope", "범위", "enum", "종목별로 계산할지, 동일가중 시장 지수 한 줄(MARKET)로 계산할지.", "symbol",
    options=_opts(("symbol", "종목별"), ("market", "시장")),
)
_VALUE_OUT = PortSpec("value", "지표 값", "indicator", "종목별 숫자. 좋고 나쁨을 정하지 않는다.", True)
_CONDITION_IN = PortSpec("condition", "조건", "condition", "종목별 참·거짓.", True)
_OPERATOR = ParamSpec(
    "operator", "비교", "enum", "왼쪽과 오른쪽을 비교하는 방법.", "gt",
    options=_opts(("lt", "미만"), ("lte", "이하"), ("gt", "초과"), ("gte", "이상")),
)


def _indicator_spec(id: str, name: str, summary: str, params: tuple[ParamSpec, ...], fields: tuple[str, ...],
                    lookback: Callable[[Mapping[str, object]], int]) -> FunctionSpec:
    return FunctionSpec(
        id=id, name=name, category="indicator", category_label="지표", summary=summary,
        inputs=(_UNIVERSE_IN,), outputs=(_VALUE_OUT,), params=params + (_SCOPE,), role="INDICATOR",
        required_data=RequiredData(fields, 0, "1d"), lookback=lookback,
    )


_RESULT_OUT = PortSpec(
    "result", "전략 판단", "strategy_result", "공통 StrategyResult 스키마.", required=True
)
_EVENT_ENTRY = PortSpec("entry", "진입 이벤트", "event", "진입 발생 시점. 여러 개를 연결하면 합친다.",
                        required=True, multiple=True, min_connections=1)
_EVENT_EXIT = PortSpec("exit", "청산 이벤트", "event", "청산 발생 시점. 교차·손절·보유기간 등 여러 개를 합친다.",
                       required=True, multiple=True, min_connections=1)

_NEW_FUNCTIONS: tuple[FunctionSpec, ...] = (
    FunctionSpec(
        id="momentum.ma-cross", name="이동평균 교차 이벤트", category="momentum",
        category_label="모멘텀 이벤트", summary="전일과 당일의 단기·장기 평균을 비교해 교차한 순간만 출력한다.",
        inputs=(_UNIVERSE_IN,),
        outputs=(
            PortSpec("entry", "골든크로스", "event", "상향 교차가 발생한 시점.", True),
            PortSpec("exit", "데드크로스", "event", "하향 교차가 발생한 시점.", True),
        ),
        params=(
            ParamSpec("fastDays", "단기 기간", "integer", "장기보다 짧아야 한다.", 20, min=1),
            ParamSpec("slowDays", "장기 기간", "integer", "단기보다 길어야 한다.", 60, min=2),
        ),
        role="EVENT", required_data=RequiredData(("close",), 61, "1d"),
    ),
    FunctionSpec(
        id="strategy.state", name="상태 평가 전략", category="strategy", category_label="전략 판단",
        summary="목표 포트폴리오를 매 평가 시점의 목표 보유 상태로 바꾼다. 비중은 다시 계산하지 않는다.",
        inputs=(
            _UNIVERSE_IN,
            PortSpec("portfolio", "목표 포트폴리오", "target_portfolio",
                     "종목별 목표 비중. 없는 종목과 유니버스에서 빠진 보유 종목은 목표 0.", True),
        ),
        outputs=(_RESULT_OUT,), params=(), role="STRATEGY", strategy_execution_type="STATE_REBALANCE",
    ),
    FunctionSpec(
        id="strategy.event", name="이벤트 생애주기 전략", category="strategy", category_label="전략 판단",
        summary="진입·청산 이벤트와 현재 포지션을 비교해 한 번의 전이만 발생시킨다.",
        inputs=(_UNIVERSE_IN, _EVENT_ENTRY, _EVENT_EXIT), outputs=(_RESULT_OUT,), params=(),
        role="STRATEGY", strategy_execution_type="EVENT_LIFECYCLE",
    ),
    FunctionSpec(
        id="strategy.hybrid", name="상태·이벤트 혼합 전략", category="strategy", category_label="전략 판단",
        summary="상태 조건으로 진입 가능성을 제한하고 이벤트로 진입·청산한다.",
        inputs=(
            _UNIVERSE_IN,
            PortSpec("eligible", "투자 가능 상태", "condition", "진입을 허용하는 지속 조건.", True),
            _EVENT_ENTRY, _EVENT_EXIT,
        ),
        outputs=(_RESULT_OUT,), params=(), role="STRATEGY", strategy_execution_type="HYBRID",
    ),
    FunctionSpec(
        id="indicator.rsi",
        name="RSI",
        category="indicator",
        category_label="지표",
        summary="종목별 RSI 값을 계산한다. 좋고 나쁨을 정하지 않는다.",
        inputs=(_UNIVERSE_IN,),
        outputs=(
            PortSpec("value", "RSI", "indicator", "0에서 100 사이의 RSI. 선호 점수가 아니다.", True),
        ),
        params=(
            ParamSpec("period", "기간", "integer", "RSI 계산에 쓰는 봉 수.", 14, min=2, unit="일"),
            _SCOPE,
        ),
        role="INDICATOR",
        required_data=RequiredData(("close",), 15, "1d"),
        lookback=lambda p: int(p["period"]) + 1,
    ),
    FunctionSpec(
        id="condition.threshold",
        name="지표 임계 조건",
        category="condition",
        category_label="조건",
        summary="지표 값이 임계값과 비교해 참인지 거짓인지만 정한다. 점수와 비중은 만들지 않는다.",
        inputs=(
            PortSpec("value", "지표 값", "indicator", "비교할 지표.", True),
        ),
        outputs=(
            PortSpec("condition", "조건", "condition", "비교 결과를 만족하면 참.", True),
        ),
        params=(
            ParamSpec(
                "operator", "비교", "enum", "지표와 임계값을 비교하는 방법.", "lt",
                options=_opts(("lt", "미만"), ("lte", "이하"), ("gt", "초과"), ("gte", "이상")),
            ),
            ParamSpec("threshold", "임계값", "number", "비교 기준값. RSI 30은 30이다.", 30),
        ),
        role="CONDITION",
    ),
    FunctionSpec(
        id="selection.top-n",
        name="상위 N 선정",
        category="selection",
        category_label="선정",
        summary="점수가 높은 순서로 N개를 고른다. 점수를 다시 계산하지 않는다.",
        inputs=(
            PortSpec("score", "종목 점수", "score", "높을수록 우선하는 점수.", True),
        ),
        outputs=(
            PortSpec("selection", "선정 종목", "selection", "순위와 점수가 보존된 선정 결과.", True),
        ),
        params=(
            ParamSpec("topN", "상위 종목 수", "integer", "남길 종목 수.", 20, min=1, unit="개"),
            ParamSpec("bufferN", "보유 유지 버퍼", "integer",
                      "보유 중인 종목은 순위가 상위 종목 수 + 버퍼 안이면 계속 남긴다. 0이면 매번 새로 고른다.",
                      0, min=0, unit="개"),
        ),
        role="SELECTION",
    ),
    FunctionSpec(
        id="selection.threshold",
        name="점수 임계 선정",
        category="selection",
        category_label="선정",
        summary="점수가 기준 이상인 종목만 남긴다. 비중은 정하지 않는다.",
        inputs=(
            PortSpec("score", "종목 점수", "score", "높을수록 우선하는 점수.", True),
        ),
        outputs=(
            PortSpec("selection", "선정 종목", "selection", "기준을 통과한 종목.", True),
        ),
        params=(
            ParamSpec("minScore", "최소 점수", "number", "이 값 이상인 종목만 선정한다.", 0),
        ),
        role="SELECTION",
    ),
    FunctionSpec(
        id="portfolio.max-weight",
        name="종목 비중 상한",
        category="portfolio",
        category_label="포트폴리오 제약",
        summary="한 종목 비중을 상한까지만 남긴다. 남는 비중은 다시 나누지 않는다.",
        inputs=(
            PortSpec("weight", "배분 비중", "weight", "제약 전 비중.", True),
        ),
        outputs=(
            PortSpec("weight", "제한된 비중", "weight", "상한이 적용된 비중. 주문 수량은 없다.", True),
        ),
        params=(
            ParamSpec("maxWeightPct", "종목 상한", "number", "한 종목이 넘지 못하는 비중.", 10, min=0, max=100, unit="%"),
        ),
        role="CONSTRAINT",
    ),
    FunctionSpec(
        id="portfolio.cash",
        name="현금 비중",
        category="portfolio",
        category_label="포트폴리오 제약",
        summary="투자 비중 합계가 1에서 현금 비중을 뺀 값이 되도록 축소한다.",
        inputs=(
            PortSpec("weight", "배분 비중", "weight", "축소할 비중.", True),
        ),
        outputs=(
            PortSpec("weight", "현금 반영 비중", "weight", "현금 비중을 남긴 종목 비중.", True),
        ),
        params=(
            ParamSpec("cashWeightPct", "현금 비중", "number", "투자하지 않고 남길 비율.", 0, min=0, max=100, unit="%"),
        ),
        role="CONSTRAINT",
    ),
    FunctionSpec(
        id="portfolio.target",
        name="목표 포트폴리오",
        category="portfolio",
        category_label="목표 포트폴리오",
        summary="배분 비중을 목표 비중과 현금으로 확정한다. 주문 수량은 만들지 않는다.",
        inputs=(
            PortSpec("weight", "배분 비중", "weight", "제약이 끝난 종목 비중.", True),
        ),
        outputs=(
            PortSpec("portfolio", "목표 포트폴리오", "target_portfolio", "종목별 목표 비중과 현금 비중.", True),
        ),
        params=(),
        role="TARGET",
    ),
    FunctionSpec(
        id="execution.orders",
        name="주문 실행",
        category="execution",
        category_label="실행",
        summary="전략 판단을 주문 수량으로 바꿔 다음 시가에 체결한다. 언제 다시 맞출지를 여기서만 정한다.",
        inputs=(
            PortSpec("result", "전략 판단", "strategy_result", "주문으로 바꿀 전략 판단 결과.", True),
        ),
        outputs=(),
        params=(
            ParamSpec(
                "rebalanceFrequency", "리밸런스", "enum",
                "상태 평가형 전략이 비중을 다시 맞추는 주기. 각 주기의 마지막 평가일에 맞춘다. 이벤트형 전략은 이벤트가 날 때 주문한다.",
                "monthly",
                options=_opts(("weekly", "주간"), ("monthly", "월간"), ("quarterly", "분기"), ("semiannual", "반기")),
            ),
            ParamSpec(
                "driftThresholdPct", "이탈 허용", "number",
                "주기가 아니어도 실제 비중이 목표에서 이만큼 벗어나면 맞춘다. 0이면 쓰지 않는다.",
                0, min=0, max=100, unit="%p",
            ),
            ParamSpec("allowFractional", "소수 주문", "boolean", "1주 미만 수량을 허용한다.", False),
        ),
        role="EXECUTION",
    ),
)

_ATOMIC_FUNCTIONS: tuple[FunctionSpec, ...] = (
    _indicator_spec(
        "indicator.return", "수익률", "최근 제외 기간을 뺀 측정 기간 수익률. 모멘텀 점수의 재료다.",
        (
            ParamSpec("lookbackDays", "측정 기간", "integer", "수익률을 재는 거래일 수.", 252, min=1, unit="일"),
            ParamSpec("skipDays", "최근 제외", "integer", "단기 반전을 피하려고 뺄 최근 거래일 수.", 0, min=0, unit="일"),
        ),
        ("close",), lambda p: int(p["lookbackDays"]) + int(p["skipDays"]) + 1,
    ),
    _indicator_spec(
        "indicator.moving-average", "이동평균", "종가의 단순이동평균.",
        (ParamSpec("windowDays", "기간", "integer", "평균을 내는 거래일 수.", 20, min=1, unit="일"),),
        ("close",), lambda p: int(p["windowDays"]),
    ),
    _indicator_spec(
        "indicator.price", "가격", "기준 시점 마지막 봉의 가격.",
        (ParamSpec("field", "가격 종류", "enum", "쓸 가격.", "close",
                   options=_opts(("close", "종가"), ("open", "시가"), ("high", "고가"), ("low", "저가"))),),
        ("open", "high", "low", "close"), lambda p: 1,
    ),
    _indicator_spec(
        "indicator.high", "기간 고점", "오늘을 뺀 직전 기간 고가의 최고치. 오늘 종가와 비교하면 돌파 조건이 된다.",
        (ParamSpec("windowDays", "기간", "integer", "고점을 찾는 거래일 수.", 252, min=1, unit="일"),),
        ("high",), lambda p: int(p["windowDays"]) + 1,
    ),
    _indicator_spec(
        "indicator.volatility", "변동성", "일간 수익률 표준편차 또는 동일가중 시장 대비 베타.",
        (
            ParamSpec("windowDays", "기간", "integer", "변동을 재는 거래일 수.", 60, min=2, unit="일"),
            ParamSpec("metric", "지표", "enum", "표준편차 또는 베타.", "stdev",
                      options=_opts(("stdev", "표준편차"), ("beta", "베타"))),
        ),
        ("close",), lambda p: int(p["windowDays"]) + 1,
    ),
    FunctionSpec(
        id="score.from-indicator", name="점수화", category="score", category_label="점수",
        summary="지표를 '높을수록 선호' 점수로 바꾼다. 낮을수록 좋은 지표는 방향을 뒤집는다.",
        inputs=(PortSpec("value", "지표 값", "indicator", "점수로 바꿀 지표.", True),),
        outputs=(PortSpec("score", "점수", "score", "높을수록 선호. raw_value에 지표 값이 남는다.", True),),
        params=(
            ParamSpec("direction", "방향", "enum", "지표가 높을수록 좋은지 낮을수록 좋은지.", "higher",
                      options=_opts(("higher", "높을수록 좋음"), ("lower", "낮을수록 좋음"))),
            ParamSpec("method", "방식", "enum", "값 그대로, 0~1 백분위, 표준점수. 점수를 합성하려면 백분위나 표준점수로 단위를 맞춘다.",
                      "raw", options=_opts(("raw", "값 그대로"), ("rank", "백분위"), ("zscore", "표준점수"))),
        ),
        role="SCORE",
    ),
    FunctionSpec(
        id="score.combine", name="점수 합성", category="score", category_label="점수",
        summary="두 점수를 비율로 더한다. 셋 이상은 이어 붙인다. 양쪽에 모두 점수가 있는 종목만 남는다.",
        inputs=(
            PortSpec("left", "왼쪽 점수", "score", "비율을 정하는 기준 점수.", True),
            PortSpec("right", "오른쪽 점수", "score", "함께 더할 점수.", True),
        ),
        outputs=(PortSpec("score", "합성 점수", "score", "왼쪽×비율 + 오른쪽×(1-비율).", True),),
        params=(ParamSpec("leftWeightPct", "왼쪽 비율", "number", "왼쪽 점수의 비중.", 50, min=0, max=100, unit="%"),),
        role="SCORE",
    ),
    FunctionSpec(
        id="condition.compare", name="지표 비교 조건", category="condition", category_label="조건",
        summary="두 지표를 비교해 참·거짓을 낸다. 가격 > 이동평균, 단기 평균 > 장기 평균, 종가 ≥ 고점 같은 조건이다.",
        inputs=(
            PortSpec("left", "왼쪽 지표", "indicator", "비교의 왼쪽.", True),
            PortSpec("right", "오른쪽 지표", "indicator", "비교의 오른쪽. 시장 한 줄이면 모든 종목에 펼친다.", True),
        ),
        outputs=(PortSpec("condition", "조건", "condition", "비교를 만족하면 참.", True),),
        params=(
            _OPERATOR,
            ParamSpec("offsetPct", "여유", "number", "오른쪽에 (1 + 여유%)를 곱해 비교한다. 돌파 여유 등에 쓴다.", 0, unit="%"),
        ),
        role="CONDITION",
    ),
    FunctionSpec(
        id="logic.not", name="NOT", category="logic", category_label="논리",
        summary="조건의 참·거짓을 뒤집는다.",
        inputs=(_CONDITION_IN,),
        outputs=(PortSpec("condition", "반대 조건", "condition", "참과 거짓이 바뀐 조건.", True),),
        params=(), role="LOGIC",
    ),
    FunctionSpec(
        id="universe.filter", name="조건 필터", category="universe", category_label="유니버스",
        summary="조건이 참인 종목만 유니버스에 남긴다. 추세 필터를 통과한 종목에서만 팩터를 고를 때 쓴다.",
        inputs=(_UNIVERSE_IN, _CONDITION_IN),
        outputs=(PortSpec("universe", "종목 집합", "universe", "조건을 통과한 종목.", True),),
        params=(), role="UNIVERSE",
    ),
    FunctionSpec(
        id="selection.condition", name="조건 선정", category="selection", category_label="선정",
        summary="조건이 참인 종목을 모두 선정한다. 순위는 종목 id 순이다.",
        inputs=(_CONDITION_IN,),
        outputs=(PortSpec("selection", "선정 종목", "selection", "조건이 참인 종목.", True),),
        params=(), role="SELECTION",
    ),
    FunctionSpec(
        id="allocation.indicator-weight", name="지표 비례 비중", category="allocation", category_label="비중",
        summary="지표에 비례하거나 반비례해 선정 종목 비중을 나눈다. 변동성에 반비례하면 역변동성 배분이다.",
        inputs=(
            PortSpec("selection", "선정 종목", "selection", "비중을 나눌 종목.", True),
            PortSpec("value", "지표 값", "indicator", "비중의 기준. 양수여야 한다.", True),
        ),
        outputs=(PortSpec("weight", "배분 비중", "weight", "합이 1인 비중.", True),),
        params=(ParamSpec("direction", "방향", "enum", "지표에 반비례 또는 비례.", "inverse",
                          options=_opts(("inverse", "반비례"), ("proportional", "비례"))),),
        role="WEIGHTING",
    ),
    FunctionSpec(
        id="portfolio.risk-off", name="국면 전환", category="portfolio", category_label="포트폴리오 제약",
        summary="조건이 거짓인 종목 비중을 줄여 현금으로 둔다. 시장 조건이면 전체에 같이 적용된다.",
        inputs=(
            PortSpec("weight", "배분 비중", "weight", "줄일 비중.", True),
            PortSpec("condition", "투자 조건", "condition", "참이면 그대로, 거짓이면 줄인다. 예: 시장 > 시장 200일선.", True),
        ),
        outputs=(PortSpec("weight", "조정된 비중", "weight", "줄어든 만큼은 현금이 된다.", True),),
        params=(ParamSpec("keepWeightPct", "유지 비율", "number", "조건이 거짓일 때 남길 비중 비율. 0이면 전부 현금.",
                          0, min=0, max=100, unit="%"),),
        role="CONSTRAINT",
    ),
    FunctionSpec(
        id="event.transition", name="상태 전이 이벤트", category="event", category_label="이벤트",
        summary="조건이 전 거래일 거짓에서 오늘 참이 되면 진입, 참에서 거짓이 되면 청산 이벤트를 낸다.",
        inputs=(_CONDITION_IN,),
        outputs=(
            PortSpec("entry", "진입", "event", "거짓 → 참이 된 시점.", True),
            PortSpec("exit", "청산", "event", "참 → 거짓이 된 시점.", True),
        ),
        params=(), role="EVENT", needs_previous=True,
    ),
    FunctionSpec(
        id="event.stop", name="손절·익절 청산", category="event", category_label="이벤트",
        summary="보유 종목이 평균진입가(또는 진입 후 고점) 대비 손절선 아래이거나 익절선 위면 청산 이벤트를 낸다.",
        inputs=(_UNIVERSE_IN,),
        outputs=(PortSpec("exit", "청산", "event", "손절 또는 익절 시점.", True),),
        params=(
            ParamSpec("stopLossPct", "손절", "number", "이 비율 이상 하락하면 청산. 0이면 쓰지 않는다.", 10, min=0, unit="%"),
            ParamSpec("takeProfitPct", "익절", "number", "진입가 대비 이 비율 이상 상승하면 청산. 0이면 쓰지 않는다.", 0, min=0, unit="%"),
            ParamSpec("trailing", "고점 대비 손절", "boolean", "진입가 대신 진입 후 고점 대비 하락으로 손절한다.", False),
        ),
        role="EVENT", required_data=RequiredData(("high", "close"), 1, "1d"),
    ),
    FunctionSpec(
        id="event.holding-period", name="보유기간 청산", category="event", category_label="이벤트",
        summary="진입 후 거래일 수가 기준 이상이면 청산 이벤트를 낸다.",
        inputs=(_UNIVERSE_IN,),
        outputs=(PortSpec("exit", "청산", "event", "보유기간이 찬 시점.", True),),
        params=(ParamSpec("maxDays", "보유 기간", "integer", "이 거래일 수가 지나면 청산.", 20, min=1, unit="일"),),
        role="EVENT",
    ),
)

FUNCTIONS: tuple[FunctionSpec, ...] = tuple(
    replace(fn, role=_legacy_role(fn), required_data=_REQUIRED_DATA.get(fn.id, RequiredData()))
    for fn in _LEGACY_FUNCTIONS
) + _NEW_FUNCTIONS[:-1] + _ATOMIC_FUNCTIONS + _NEW_FUNCTIONS[-1:]

_BY_ID = {fn.id: fn for fn in FUNCTIONS}


def get_function(function_id: str) -> FunctionSpec:
    try:
        return _BY_ID[function_id]
    except KeyError as error:
        raise KeyError(f"알 수 없는 함수입니다: {function_id}") from error


def instance_id(function_id: str) -> str:
    """함수 id의 점과 하이픈을 밑줄로 바꾼 인스턴스 id. 두 번째 블록은 `_2`를 붙인다."""
    return function_id.replace(".", "_").replace("-", "_")


def _port_dict(port: PortSpec) -> dict[str, object]:
    return {
        "key": port.key,
        "label": port.label,
        "type": port.type,
        "typeLabel": PORT_TYPE_LABEL[port.type],
        "description": port.description,
        "required": port.required,
        "multiple": port.multiple,
        "minConnections": port.min_connections,
    }


def _param_dict(param: ParamSpec) -> dict[str, object]:
    return {
        "key": param.key,
        "label": param.label,
        "type": param.type,
        "description": param.description,
        "default": param.default,
        "options": [{"value": option.value, "label": option.label} for option in param.options],
        "unit": param.unit,
        "min": param.min,
        "max": param.max,
    }


def function_catalog() -> dict[str, object]:
    """팔레트가 그리는 함수 정의. 인스턴스 JSON은 캔버스에 놓을 때의 기본값이다."""
    functions = []
    for fn in FUNCTIONS:
        params = {param.key: param.default for param in fn.params if param.default is not None}
        functions.append({
            "id": fn.id,
            "name": fn.name,
            "category": fn.category,
            "categoryLabel": fn.category_label,
            "summary": fn.summary,
            "inputs": [_port_dict(port) for port in fn.inputs],
            "outputs": [_port_dict(port) for port in fn.outputs],
            "params": [_param_dict(param) for param in fn.params],
            "instance": {
                "id": instance_id(fn.id),
                "function_id": fn.id,
                "params": params,
            },
        })
    return {"functions": functions}
