from dataclasses import replace

from ai_studio.schema import FunctionSpec, ParamOption, ParamSpec, PortSpec, RequiredData

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
        id="momentum.factor",
        name="모멘텀 팩터 점수",
        category="momentum",
        category_label="모멘텀 신호",
        summary="과거 수익률이 높은 종목에 높은 점수를 준다.",
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
            ParamSpec(
                "topN",
                "상위 종목 수",
                "integer",
                "점수가 높은 종목만 남긴다.",
                default=20,
                min=1,
                unit="개",
            ),
        ),
    ),
    FunctionSpec(
        id="momentum.trend",
        name="추세 상태 조건",
        category="momentum",
        category_label="모멘텀 신호",
        summary="이동평균 배열이나 가격 위치로 상승 추세 종목을 고른다.",
        inputs=(_UNIVERSE_IN,),
        outputs=(
            PortSpec(
                "signal",
                "추세 신호",
                "signal",
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
        category_label="모멘텀 신호",
        summary="정해진 기간의 고점을 돌파한 종목에 매수 신호를 준다.",
        inputs=(_UNIVERSE_IN,),
        outputs=(
            PortSpec(
                "signal",
                "돌파 신호",
                "signal",
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
        summary="선택된 종목을 동일 비중, 점수 비중, 또는 지정 비중으로 나눈다.",
        inputs=(
            PortSpec(
                "selection",
                "선택 결과",
                "score",
                "비중을 나눌 종목. 종목 집합, 신호, 점수 중 하나를 받는다.",
                required=True,
                accepts=("universe", "signal", "score"),
            ),
        ),
        outputs=(
            PortSpec(
                "weight",
                "목표 비중",
                "weight",
                "종목별 목표 비중. 합계는 1에서 현금 비중을 뺀 값이다.",
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
                    ("fixed", "종목당 고정 비중"),
                ),
            ),
            ParamSpec(
                "maxWeightPct",
                "종목 상한",
                "number",
                "한 종목이 넘지 못하는 비중.",
                default=10,
                min=0,
                max=100,
                unit="%",
            ),
            ParamSpec(
                "cashWeightPct",
                "현금 비중",
                "number",
                "투자하지 않고 남겨 둘 현금 비율.",
                default=0,
                min=0,
                max=100,
                unit="%",
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
        summary="저평가된 종목에 높은 점수를 준다.",
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
            ParamSpec(
                "topN",
                "상위 종목 수",
                "integer",
                "가치 점수가 높은 종목만 남긴다.",
                default=30,
                min=1,
                unit="개",
            ),
        ),
    ),
    FunctionSpec(
        id="factor.quality",
        name="퀄리티 팩터",
        category="factor",
        category_label="팩터 전략",
        summary="수익성과 재무 건전성이 좋은 종목에 높은 점수를 준다.",
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
            ParamSpec(
                "topN",
                "상위 종목 수",
                "integer",
                "퀄리티 점수가 높은 종목만 남긴다.",
                default=30,
                min=1,
                unit="개",
            ),
        ),
    ),
    FunctionSpec(
        id="factor.low-volatility",
        name="저변동성 팩터",
        category="factor",
        category_label="팩터 전략",
        summary="가격 변동이 작은 종목에 높은 점수를 준다.",
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
            ParamSpec(
                "bottomN",
                "저변동 종목 수",
                "integer",
                "변동이 작은 종목만 남긴다.",
                default=30,
                min=1,
                unit="개",
            ),
        ),
    ),
    FunctionSpec(
        id="logic.combine",
        name="복수 조건 조합",
        category="logic",
        category_label="논리",
        summary="여러 매수·매도 신호를 AND 또는 OR로 하나로 합친다.",
        inputs=(
            PortSpec(
                "signals",
                "신호",
                "signal",
                "합칠 신호. 두 개 이상 연결한다.",
                required=True,
                multiple=True,
                min_connections=2,
            ),
        ),
        outputs=(
            PortSpec(
                "signal",
                "결합 신호",
                "signal",
                "조합 규칙으로 합쳐진 참·거짓.",
                required=True,
            ),
        ),
        params=(
            ParamSpec(
                "operator",
                "조합",
                "enum",
                "모든 신호가 참이어야 하는지, 하나면 충분한지.",
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
        summary="조건 신호가 참이면 한쪽 결과를, 거짓이면 다른 쪽 결과를 내보낸다.",
        inputs=(
            PortSpec(
                "condition",
                "조건",
                "signal",
                "어느 분기를 쓸지 정하는 참·거짓 신호.",
                required=True,
            ),
            PortSpec(
                "thenBranch",
                "참일 때",
                "score",
                "조건이 참일 때 내보낼 값.",
                required=True,
                accepts=("universe", "signal", "score", "weight"),
            ),
            PortSpec(
                "elseBranch",
                "거짓일 때",
                "score",
                "조건이 거짓일 때 내보낼 값. 참일 때와 같은 타입이어야 한다.",
                required=True,
                accepts=("universe", "signal", "score", "weight"),
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
                accepts=("universe", "signal", "score", "weight"),
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
        summary="두 종목 집합 또는 두 신호를 합집합, 교집합, 차집합으로 합친다.",
        inputs=(
            PortSpec(
                "left",
                "왼쪽 집합",
                "universe",
                "연산의 기준 집합.",
                required=True,
                accepts=("universe", "signal"),
            ),
            PortSpec(
                "right",
                "오른쪽 집합",
                "universe",
                "기준 집합과 합칠 집합. 왼쪽과 같은 타입이어야 한다.",
                required=True,
                accepts=("universe", "signal"),
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
                accepts=("universe", "signal"),
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
        return "INDICATOR"
    if fn.category == "logic":
        return "LOGIC"
    if fn.category == "allocation":
        return "ALLOCATION"
    return "CONDITION"


_RESULT_OUT = PortSpec(
    "result", "전략 판단", "strategy_result", "공통 StrategyResult 스키마.", required=True
)
_EVENT_ENTRY = PortSpec("entry", "진입 이벤트", "event", "진입 발생 시점만 참.", required=True)
_EVENT_EXIT = PortSpec("exit", "청산 이벤트", "event", "청산 발생 시점만 참.", required=True)

_NEW_FUNCTIONS: tuple[FunctionSpec, ...] = (
    FunctionSpec(
        id="momentum.ma-cross", name="이동평균 교차 이벤트", category="momentum",
        category_label="모멘텀 신호", summary="전일과 당일의 단기·장기 평균을 비교해 교차한 순간만 출력한다.",
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
        summary="조건의 현재 상태를 매 평가 시점에 목표 보유 상태로 바꾼다.",
        inputs=(_UNIVERSE_IN, PortSpec("condition", "상태 조건", "signal", "참이면 선정, 거짓이면 목표 비중 0.", True)),
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
            PortSpec("eligible", "투자 가능 상태", "signal", "진입을 허용하는 지속 조건.", True),
            _EVENT_ENTRY, _EVENT_EXIT,
        ),
        outputs=(_RESULT_OUT,), params=(), role="STRATEGY", strategy_execution_type="HYBRID",
    ),
)

FUNCTIONS: tuple[FunctionSpec, ...] = tuple(
    replace(fn, role=_legacy_role(fn), required_data=_REQUIRED_DATA.get(fn.id, RequiredData()))
    for fn in _LEGACY_FUNCTIONS
) + _NEW_FUNCTIONS

_BY_ID = {fn.id: fn for fn in FUNCTIONS}


def get_function(function_id: str) -> FunctionSpec:
    try:
        return _BY_ID[function_id]
    except KeyError as error:
        raise KeyError(f"알 수 없는 함수입니다: {function_id}") from error
