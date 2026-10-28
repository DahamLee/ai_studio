"""블록 카탈로그. 모든 블록은 하나의 역할(role)만 가진다.

단계: UNIVERSE → INDICATOR → SCORE·CONDITION → SELECTION·EVENT → WEIGHTING → CONSTRAINT → TARGET
→ STRATEGY(전략 판단) → EXECUTION. 팩터 갈래와 시그널 갈래는 같은 단계를 공유하고, 전략은 필요한 단계만 쓴다.
여러 단계를 묶은 자주 쓰는 조합은 블록이 아니라 n12_strategies의 프리셋이다.
"""

from collections.abc import Callable, Mapping
from dataclasses import replace

from ai_studio.n03_market_data import FUNDAMENTAL_ACCOUNTS
from ai_studio.n02_schema import (
    LANES, PORT_TYPE_LABEL, RUN_CONTEXT, STAGES, FunctionSpec, ParamOption, ParamSpec, PortSpec, RequiredData, stage_of,
)


def _opts(*pairs: tuple[str, str]) -> tuple[ParamOption, ...]:
    return tuple(ParamOption(value, label) for value, label in pairs)


_UNIVERSE_IN = PortSpec("universe", "종목 집합", "universe", "앞 단계에서 넘어온 투자 대상.", required=True)
_UNIVERSE_OUT = PortSpec("universe", "종목 집합", "universe", "조건을 통과한 종목 목록.", required=True)
_VALUE_OUT = PortSpec("value", "지표 값", "indicator", "종목별 숫자. 좋고 나쁨을 정하지 않는다.", True)
_CONDITION_IN = PortSpec("condition", "조건", "condition", "종목별 참·거짓.", True)
_SCORE_IN = PortSpec("score", "종목 점수", "score", "높을수록 우선하는 점수.", True)
_WEIGHT_IN = PortSpec("weight", "배분 비중", "weight", "앞 단계의 종목 비중.", True)
_SELECTION_OUT = PortSpec("selection", "선정 종목", "selection", "순위와 점수가 보존된 선정 결과.", True)
_RESULT_OUT = PortSpec("result", "전략 판단", "strategy_result", "공통 StrategyResult 스키마.", True)
_SCOPE = ParamSpec(
    "scope", "범위", "enum", "종목별로 계산할지, 동일가중 시장 지수 한 줄(MARKET)로 계산할지.", "symbol",
    options=_opts(("symbol", "종목별"), ("market", "시장")),
)
_OPERATOR = ParamSpec(
    "operator", "비교", "enum", "왼쪽과 오른쪽을 비교하는 방법.", "gt",
    options=_opts(("lt", "미만"), ("lte", "이하"), ("gt", "초과"), ("gte", "이상")),
)
_FREQUENCY = _opts(("weekly", "주간"), ("monthly", "월간"), ("quarterly", "분기"), ("semiannual", "반기"))


def _indicator(id: str, name: str, summary: str, params: tuple[ParamSpec, ...], fields: tuple[str, ...],
               lookback: Callable[[Mapping[str, object]], int], *, scoped: bool = True) -> FunctionSpec:
    return FunctionSpec(
        id=id, name=name, category="indicator", category_label="지표", summary=summary,
        inputs=(_UNIVERSE_IN,), outputs=(_VALUE_OUT,), params=params + ((_SCOPE,) if scoped else ()),
        role="INDICATOR", required_data=RequiredData(fields, 0, "1d"), lookback=lookback,
    )


_UNIVERSE: tuple[FunctionSpec, ...] = (
    FunctionSpec(
        id="universe.compose", name="종목 불러오기", category="universe", category_label="유니버스",
        summary="시장과 지수로 투자 대상 종목 집합을 만든다. 기준일에 아직 상장하지 않은 종목은 넣지 않는다. 규모 조건은 시가총액으로 거르기를 쓴다.",
        inputs=(), outputs=(_UNIVERSE_OUT,),
        params=(
            ParamSpec("market", "시장", "enum", "종목을 가져올 시장.", "US_EQUITY",
                      options=_opts(("US_EQUITY", "미국 주식"), ("KR_EQUITY", "국내 주식"), ("ETF", "ETF"))),
            ParamSpec("benchmark", "기준 지수", "enum",
                      "유니버스의 모집단이 되는 지수. 선택한 시장에 있는 지수만 화면에 보여 준다.", "SPX",
                      options=_opts(("SPX", "S&P 500"), ("NDX", "나스닥 100"), ("KOSPI", "코스피"),
                                    ("KOSDAQ", "코스닥"), ("ETF_LISTED", "상장 ETF"))),
        ),
        role="UNIVERSE",
    ),
    FunctionSpec(
        id="universe.exclude", name="투자 제외하기", category="universe", category_label="유니버스",
        summary="종목 집합에서 거래 불가·저유동성·상장 초기 종목을 걸러 낸다.",
        inputs=(_UNIVERSE_IN,), outputs=(_UNIVERSE_OUT,),
        params=(
            ParamSpec("excludeFinancials", "금융주 제외", "boolean", "은행·보험·증권 등 금융 업종을 뺀다.", False),
            ParamSpec("excludeLossMaking", "적자 기업 제외", "boolean", "최근 결산 순이익이 0 이하인 종목을 뺀다.", False),
            ParamSpec("minAverageTurnover", "최소 평균 거래대금", "number",
                      "최근 20거래일 평균 거래대금(종가×거래량)이 이 값보다 작으면 제외한다. 통화는 시세와 같다.",
                      0, min=0, unit="달러"),
            ParamSpec("minListingDays", "최소 상장일수", "integer", "상장 후 이 거래일 수보다 짧은 종목을 제외한다.",
                      252, min=0, unit="일"),
            ParamSpec("excludeHalted", "거래정지 제외", "boolean", "신호일에 봉이 없는 종목을 뺀다.", True),
        ),
        role="UNIVERSE", required_data=RequiredData(("close", "volume"), 0, "1d"),
        lookback=lambda p: int(p["minListingDays"]),
    ),
    FunctionSpec(
        id="universe.fundamental", name="재무 조건으로 거르기", category="universe", category_label="유니버스",
        summary="평가일까지 공시된 가장 최근 분기 재무값으로 종목을 거른다. 공시일(업로드일) 기준이라 아직 나오지 않은 실적은 보지 않는다. 공시가 없는 종목은 뺀다. 조건이 여럿이면 이 노드를 이어 붙인다.",
        inputs=(_UNIVERSE_IN,), outputs=(_UNIVERSE_OUT,),
        params=(
            ParamSpec("account", "계정", "enum", "비교할 재무 계정.", "ROE",
                      options=_opts(*((code, f"{label} ({unit})") for code, (label, unit) in FUNDAMENTAL_ACCOUNTS.items()))),
            replace(_OPERATOR, default="gte", description="계정 값과 기준값을 비교하는 방법."),
            ParamSpec("threshold", "기준값", "number",
                      "계정 단위 그대로 쓴다. ROE·ROA·부채비율은 퍼센트 값(10%는 10), 금액은 백만 달러.", 10),
        ),
        role="UNIVERSE", required_data=RequiredData(("fundamental",), 0, "1q"),
    ),
    FunctionSpec(
        id="universe.market-cap", name="시가총액으로 거르기", category="universe", category_label="유니버스",
        summary="시가총액(그날 종가 × 최근 공시 발행주식수)으로 종목을 거른다. 하한 아래를 빼고, 상위 N개를 주면 큰 순서로 그만큼만 남긴다.",
        inputs=(_UNIVERSE_IN,), outputs=(_UNIVERSE_OUT,),
        params=(
            ParamSpec("minCap", "최소 시가총액", "number", "이 값보다 작은 종목을 뺀다. 0이면 제한 없음.", 0, min=0, unit="십억 달러"),
            ParamSpec("topN", "상위 종목 수", "integer", "시가총액이 큰 순서로 이만큼만 남긴다. 0이면 제한 없음.", 0, min=0, unit="개"),
        ),
        role="UNIVERSE", required_data=RequiredData(("close", "fundamental"), 1, "1d"),
    ),
    FunctionSpec(
        id="universe.min-price", name="저가주 빼기", category="universe", category_label="유니버스",
        summary="기준일 종가가 하한보다 낮은 종목을 뺀다. 가격 외의 조건은 보지 않는다.",
        inputs=(_UNIVERSE_IN,), outputs=(_UNIVERSE_OUT,),
        params=(ParamSpec("minClose", "최소 종가", "number",
                          "기준일 종가가 이 값보다 낮으면 제외한다. 0이면 제한 없음.", 0, min=0, unit="달러"),),
        role="UNIVERSE",
    ),
)

_INDICATORS: tuple[FunctionSpec, ...] = (
    _indicator(
        "indicator.return", "수익률 구하기", "최근 제외 기간을 뺀 측정 기간 수익률. 모멘텀 점수의 재료다.",
        (
            ParamSpec("lookbackDays", "측정 기간", "integer", "수익률을 재는 거래일 수.", 252, min=1, unit="일"),
            ParamSpec("skipDays", "최근 제외", "integer", "단기 반전을 피하려고 뺄 최근 거래일 수.", 0, min=0, unit="일"),
        ),
        ("close",), lambda p: int(p["lookbackDays"]) + int(p["skipDays"]) + 1,
    ),
    _indicator(
        "indicator.moving-average", "이동평균 구하기", "종가의 이동평균. 단순(SMA) 또는 지수(EMA) 방식.",
        (
            ParamSpec("windowDays", "기간", "integer", "평균을 내는 거래일 수.", 20, min=1, unit="일"),
            ParamSpec("method", "평균 방식", "enum",
                      "단순은 기간 안 종가를 똑같이 평균한다. 지수는 최근 종가에 더 큰 비중을 두며 최근 기간×3 봉으로 계산한다.",
                      "sma", options=_opts(("sma", "단순(SMA)"), ("ema", "지수(EMA)"))),
        ),
        ("close",), lambda p: int(p["windowDays"]),
    ),
    _indicator(
        "indicator.price", "가격 가져오기", "기준 시점 마지막 봉의 가격.",
        (ParamSpec("field", "가격 종류", "enum", "쓸 가격.", "close",
                   options=_opts(("close", "종가"), ("open", "시가"), ("high", "고가"), ("low", "저가"))),),
        ("open", "high", "low", "close"), lambda p: 1,
    ),
    _indicator(
        "indicator.high", "기간 고점 구하기", "오늘을 뺀 직전 기간 고가의 최고치. 오늘 종가와 비교하면 돌파 조건이 된다.",
        (ParamSpec("windowDays", "기간", "integer", "고점을 찾는 거래일 수.", 252, min=1, unit="일"),),
        ("high",), lambda p: int(p["windowDays"]) + 1,
    ),
    _indicator(
        "indicator.volatility", "변동성 구하기", "일간 수익률 표준편차 또는 동일가중 시장 대비 베타.",
        (
            ParamSpec("windowDays", "기간", "integer", "변동을 재는 거래일 수.", 60, min=2, unit="일"),
            ParamSpec("metric", "지표", "enum", "표준편차 또는 베타.", "stdev",
                      options=_opts(("stdev", "표준편차"), ("beta", "베타"))),
        ),
        ("close",), lambda p: int(p["windowDays"]) + 1,
    ),
    _indicator(
        "indicator.rsi", "RSI 구하기", "종목별 RSI 값을 계산한다. 좋고 나쁨을 정하지 않는다.",
        (ParamSpec("period", "기간", "integer", "RSI 계산에 쓰는 봉 수.", 14, min=2, unit="일"),),
        ("close",), lambda p: int(p["period"]) + 1,
    ),
    _indicator(
        "indicator.macd", "MACD 구하기",
        "단기·장기 지수이동평균의 차이(MACD선), 그 지수이동평균(시그널), 둘의 차이(히스토그램) 중 하나를 낸다. MACD 골든크로스는 MACD선과 시그널을 두 지표 비교하기로 잇는다.",
        (
            ParamSpec("fastDays", "단기", "integer", "단기 지수이동평균 기간.", 12, min=1, unit="일"),
            ParamSpec("slowDays", "장기", "integer", "장기 지수이동평균 기간. 단기보다 길어야 한다.", 26, min=2, unit="일"),
            ParamSpec("signalDays", "시그널", "integer", "MACD선을 평균하는 기간.", 9, min=1, unit="일"),
            ParamSpec("line", "값", "enum", "내보낼 값.", "macd",
                      options=_opts(("macd", "MACD선"), ("signal", "시그널선"), ("histogram", "히스토그램"))),
        ),
        ("close",), lambda p: int(p["slowDays"]) + int(p["signalDays"]) - 1,
    ),
    _indicator(
        "indicator.bollinger", "볼린저 밴드 구하기",
        "이동평균 ± 표준편차 배수로 만든 밴드. 상단·하단 가격, %B(밴드 안 위치, 0=하단·1=상단), 밴드폭(%) 중 하나를 낸다. 가운데 선은 이동평균 구하기를 쓴다.",
        (
            ParamSpec("windowDays", "기간", "integer", "평균과 표준편차를 내는 거래일 수.", 20, min=2, unit="일"),
            ParamSpec("width", "폭", "number", "표준편차의 몇 배로 밴드를 그을지.", 2, min=0.1, unit="배"),
            ParamSpec("line", "값", "enum", "내보낼 값.", "upper",
                      options=_opts(("upper", "상단"), ("lower", "하단"), ("percent_b", "%B"), ("bandwidth", "밴드폭(%)"))),
        ),
        ("close",), lambda p: int(p["windowDays"]),
    ),
    _indicator(
        "indicator.atr", "ATR 구하기",
        "하루 가격 변동폭(전일 종가와의 갭 포함)의 평균. 가격 단위 또는 종가 대비 %로 낸다. 손절 폭이나 변동성 조건에 쓴다.",
        (
            ParamSpec("periodDays", "기간", "integer", "평균을 내는 거래일 수.", 14, min=1, unit="일"),
            ParamSpec("unit", "단위", "enum", "가격 그대로 또는 종가 대비 퍼센트.", "price",
                      options=_opts(("price", "가격"), ("percent", "종가 대비 %"))),
        ),
        ("high", "low", "close"), lambda p: int(p["periodDays"]) + 1,
    ),
    _indicator(
        "indicator.fundamental", "재무 지표 가져오기",
        "평가일까지 공시된(업로드일 기준) 재무값. PER·PBR·PSR은 그날 종가로 계산한다. 공시가 모자라거나 적자로 PER을 정할 수 없는 종목은 값이 없다.",
        (ParamSpec("field", "항목", "enum", "가져올 재무 항목. 비율은 퍼센트 값(ROE 10%는 10)이다.", "per",
                   options=_opts(("per", "PER (종가 ÷ 최근 4분기 EPS)"), ("pbr", "PBR (종가 ÷ BPS)"),
                                 ("psr", "PSR (종가 ÷ 최근 4분기 주당매출)"),
                                 ("revenue_yoy", "매출 성장률 (전년 같은 분기 대비 %)"),
                                 ("net_income_yoy", "순이익 성장률 (전년 같은 분기 대비 %)"),
                                 ("eps_yoy", "EPS 성장률 (전년 같은 분기 대비 %)"),
                                 *((code.lower(), f"{label} ({unit})") for code, (label, unit) in FUNDAMENTAL_ACCOUNTS.items()))),),
        ("close", "fundamental"), lambda p: 1, scoped=False,
    ),
)

_SCORES_AND_CONDITIONS: tuple[FunctionSpec, ...] = (
    FunctionSpec(
        id="score.from-indicator", name="점수 매기기", category="score", category_label="점수",
        summary="지표를 '높을수록 선호' 점수로 바꾼다. 낮을수록 좋은 지표는 방향을 뒤집는다.",
        inputs=(PortSpec("value", "지표 값", "indicator", "점수로 바꿀 지표.", True),),
        outputs=(PortSpec("score", "점수", "score", "높을수록 선호. raw_value에 지표 값이 남는다.", True),),
        params=(
            ParamSpec("direction", "방향", "enum", "지표가 높을수록 좋은지 낮을수록 좋은지.", "higher",
                      options=_opts(("higher", "높을수록 좋음"), ("lower", "낮을수록 좋음"))),
            ParamSpec("method", "방식", "enum",
                      "값 그대로, 0~1 백분위, 표준점수. 점수를 합성하려면 백분위나 표준점수로 단위를 맞춘다.", "raw",
                      options=_opts(("raw", "값 그대로"), ("rank", "백분위"), ("zscore", "표준점수"))),
        ),
        role="SCORE",
    ),
    FunctionSpec(
        id="score.combine", name="점수 합치기", category="score", category_label="점수",
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
        id="condition.threshold", name="기준값과 비교하기", category="condition", category_label="조건",
        summary="지표 값이 임계값과 비교해 참인지 거짓인지만 정한다. 점수와 비중은 만들지 않는다.",
        inputs=(PortSpec("value", "지표 값", "indicator", "비교할 지표.", True),),
        outputs=(PortSpec("condition", "조건", "condition", "비교 결과를 만족하면 참.", True),),
        params=(
            ParamSpec("operator", "비교", "enum", "지표와 임계값을 비교하는 방법.", "lt",
                      options=_opts(("lt", "미만"), ("lte", "이하"), ("gt", "초과"), ("gte", "이상"))),
            ParamSpec("threshold", "임계값", "number", "비교 기준값. RSI 30은 30이다.", 30),
        ),
        role="CONDITION",
    ),
    FunctionSpec(
        id="condition.compare", name="두 지표 비교하기", category="condition", category_label="조건",
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
)

_LOGIC: tuple[FunctionSpec, ...] = (
    FunctionSpec(
        id="logic.combine", name="조건 묶기", category="logic", category_label="논리",
        summary="여러 상태 조건을 AND 또는 OR로 하나로 합친다.",
        inputs=(PortSpec("conditions", "조건", "condition", "합칠 조건. 두 개 이상 연결한다.", True,
                         multiple=True, min_connections=2),),
        outputs=(PortSpec("condition", "결합 조건", "condition", "조합 규칙으로 합쳐진 참·거짓.", True),),
        params=(ParamSpec("operator", "조합", "enum", "모든 조건이 참이어야 하는지, 하나면 충분한지.", "and",
                          options=_opts(("and", "모두 참 (AND)"), ("or", "하나라도 참 (OR)"))),),
        role="LOGIC",
    ),
    FunctionSpec(
        id="logic.not", name="조건 뒤집기", category="logic", category_label="논리",
        summary="조건의 참·거짓을 뒤집는다.",
        inputs=(_CONDITION_IN,),
        outputs=(PortSpec("condition", "반대 조건", "condition", "참과 거짓이 바뀐 조건.", True),),
        params=(), role="LOGIC",
    ),
    FunctionSpec(
        id="logic.if-else", name="조건에 따라 나누기", category="logic", category_label="논리",
        summary="조건이 참이면 한쪽 결과를, 거짓이면 다른 쪽 결과를 내보낸다.",
        inputs=(
            PortSpec("condition", "조건", "condition", "어느 분기를 쓸지 정하는 참·거짓 조건.", True),
            PortSpec("thenBranch", "참일 때", "score", "조건이 참일 때 내보낼 값.", True, accepts=("condition", "score")),
            PortSpec("elseBranch", "거짓일 때", "score", "조건이 거짓일 때 내보낼 값. 참일 때와 같은 타입이어야 한다.", True,
                     accepts=("condition", "score"), same_type_as="thenBranch"),
        ),
        outputs=(PortSpec("result", "선택 결과", "score", "조건에 따라 둘 중 하나가 나온 결과. 타입은 두 분기와 같다.", True,
                          accepts=("condition", "score"), mirrors="thenBranch"),),
        params=(), role="LOGIC",
    ),
)

_SELECTION_AND_EVENTS: tuple[FunctionSpec, ...] = (
    FunctionSpec(
        id="selection.top-n", name="상위 N개 고르기", category="selection", category_label="선정",
        summary="점수가 높은 순서로 N개를 고른다. 조건을 주면 조건이 참인 종목 안에서만 고른다. 보유 유지 버퍼를 주면 조금 밀린 보유 종목을 남긴다.",
        inputs=(
            _SCORE_IN,
            PortSpec("eligible", "대상 조건", "condition",
                     "선택. 참인 종목 안에서만 고른다. 예: 종가가 50일선 위인 종목 중 모멘텀 상위 10.", False),
        ),
        outputs=(_SELECTION_OUT,),
        params=(
            ParamSpec("topN", "상위 종목 수", "integer", "남길 종목 수.", 20, min=1, unit="개"),
            ParamSpec("bufferN", "보유 유지 버퍼", "integer",
                      "보유 중인 종목은 순위가 상위 종목 수 + 버퍼 안이면 계속 남긴다. 0이면 매번 새로 고른다.",
                      0, min=0, unit="개"),
        ),
        role="SELECTION",
    ),
    FunctionSpec(
        id="selection.threshold", name="점수 기준으로 고르기", category="selection", category_label="선정",
        summary="점수가 기준 이상인 종목만 남긴다. 비중은 정하지 않는다.",
        inputs=(_SCORE_IN,),
        outputs=(PortSpec("selection", "선정 종목", "selection", "기준을 통과한 종목.", True),),
        params=(ParamSpec("minScore", "최소 점수", "number", "이 값 이상인 종목만 선정한다.", 0),),
        role="SELECTION",
    ),
    FunctionSpec(
        id="selection.condition", name="조건 맞는 종목 고르기", category="selection", category_label="선정",
        summary="조건이 참인 종목을 모두 선정한다. 순위는 종목 id 순이다.",
        inputs=(_CONDITION_IN,),
        outputs=(PortSpec("selection", "선정 종목", "selection", "조건이 참인 종목.", True),),
        params=(), role="SELECTION",
    ),
    FunctionSpec(
        id="event.transition", name="진입·청산 시점 잡기", category="event", category_label="이벤트",
        summary="조건이 전 거래일 거짓에서 오늘 참이 되면 진입, 참에서 거짓이 되면 청산 신호를 낸다. 보유·현금은 보지 않고 시점만 알린다. 실제로 살지 팔지는 매매 결정하기 단계가 정한다.",
        inputs=(_CONDITION_IN,),
        outputs=(
            PortSpec("entry", "진입", "event", "거짓 → 참이 된 시점.", True),
            PortSpec("exit", "청산", "event", "참 → 거짓이 된 시점.", True),
        ),
        params=(), role="EVENT", needs_previous=True,
    ),
    FunctionSpec(
        id="event.stop", name="손절·익절 시점 잡기", category="event", category_label="이벤트",
        summary="보유 종목이 평균진입가(또는 진입 후 고점) 대비 손절선 아래이거나 익절선 위로 가면 청산 신호를 낸다. 보유 종목만 보므로 입력이 없다.",
        inputs=(),
        outputs=(PortSpec("exit", "청산", "event", "손절 또는 익절 시점.", True),),
        params=(
            ParamSpec("stopLossPct", "손절", "number", "이 비율 이상 하락하면 청산. 0이면 쓰지 않는다.", 10, min=0, unit="%"),
            ParamSpec("takeProfitPct", "익절", "number", "진입가 대비 이 비율 이상 상승하면 청산. 0이면 쓰지 않는다.",
                      0, min=0, unit="%"),
            ParamSpec("trailing", "고점 대비 손절", "boolean", "진입가 대신 진입 후 고점 대비 하락으로 손절한다.", False),
        ),
        role="EVENT", required_data=RequiredData(("high", "close"), 1, "1d"),
    ),
    FunctionSpec(
        id="event.holding-period", name="보유기간 만료 시점 잡기", category="event", category_label="이벤트",
        summary="진입 후 거래일 수가 기준 이상이 되면 청산 신호를 낸다. 보유 종목만 보므로 입력이 없다.",
        inputs=(),
        outputs=(PortSpec("exit", "청산", "event", "보유기간이 찬 시점.", True),),
        params=(ParamSpec("maxDays", "보유 기간", "integer", "이 거래일 수가 지나면 청산.", 20, min=1, unit="일"),),
        role="EVENT",
    ),
)

_PORTFOLIO: tuple[FunctionSpec, ...] = (
    FunctionSpec(
        id="allocation.target-weight", name="비중 나누기", category="allocation", category_label="비중",
        summary="선정된 종목만 동일 비중 또는 점수 비례 비중으로 나눈다. 상한과 현금은 정하지 않는다.",
        inputs=(PortSpec("selection", "선정 종목", "selection",
                         "랭킹 단계에서 고른 종목. 점수 비례일 때는 선정 결과에 점수가 있어야 한다.", True),),
        outputs=(PortSpec("weight", "배분 비중", "weight", "제약 적용 전의 종목 비중. 주문 수량은 포함하지 않는다.", True),),
        params=(ParamSpec("scheme", "배분 방식", "enum", "종목 사이 비중을 나누는 규칙.", "equal",
                          options=_opts(("equal", "동일 비중"), ("score", "점수 비례"))),),
        role="WEIGHTING",
    ),
    FunctionSpec(
        id="allocation.indicator-weight", name="지표로 비중 나누기", category="allocation", category_label="비중",
        summary="지표에 비례하거나 반비례해 선정 종목 비중을 나눈다. 변동성에 반비례하면 역변동성 배분이다.",
        inputs=(
            PortSpec("selection", "선정 종목", "selection", "비중을 나눌 종목.", True),
            PortSpec("value", "지표 값", "indicator", "보조 입력. 비중의 기준. 양수여야 한다.", True, auxiliary=True),
        ),
        outputs=(PortSpec("weight", "배분 비중", "weight", "합이 1인 비중.", True),),
        params=(ParamSpec("direction", "방향", "enum", "지표에 반비례 또는 비례.", "inverse",
                          options=_opts(("inverse", "반비례"), ("proportional", "비례"))),),
        role="WEIGHTING",
    ),
    FunctionSpec(
        id="allocation.event-slots", name="최대 보유 수로 비중 정하기", category="allocation", category_label="비중",
        summary="신호 전략의 비중 규칙. 최대 보유 수가 N이면 한 종목 비중은 1/N이고, N종목이 다 차 있으면 새로 사지 않는다. 진입·청산 신호를 모으고, 게이트를 주면 게이트를 통과한 종목만 진입 후보가 된다.",
        inputs=(
            PortSpec("entry", "진입 신호", "event", "진입 발생 시점. 여러 개를 연결하면 합친다.", True,
                     multiple=True, min_connections=1),
            PortSpec("exit", "청산 신호", "event", "청산 발생 시점. 교차·손절·보유기간 등 여러 개를 합친다.", True,
                     multiple=True, min_connections=1),
            PortSpec("gate", "진입 게이트", "selection",
                     "선택. 선정된 종목(또는 조건이 참인 종목)만 진입한다. 선정 결과면 순위가 높은 종목부터 빈 자리를 채운다.",
                     False, accepts=("selection", "condition"), auxiliary=True),
        ),
        outputs=(PortSpec("plan", "진입·청산 계획", "event_plan", "이번 평가일의 진입·청산 종목과 자리 규칙.", True),),
        params=(ParamSpec("maxPositions", "최대 보유 수", "integer",
                          "동시에 보유할 최대 종목 수. 진입 비중은 1/최대 보유 수이고, 빈 자리만큼만 진입한다.",
                          10, min=1, unit="개"),),
        role="WEIGHTING",
    ),
    FunctionSpec(
        id="portfolio.max-weight", name="비중 상한 두기", category="portfolio", category_label="포트폴리오 제약",
        summary="한 종목 비중을 상한까지만 남긴다. 남는 비중은 다시 나누지 않는다.",
        inputs=(_WEIGHT_IN,),
        outputs=(PortSpec("weight", "제한된 비중", "weight", "상한이 적용된 비중. 주문 수량은 없다.", True),),
        params=(ParamSpec("maxWeightPct", "종목 상한", "number", "한 종목이 넘지 못하는 비중.", 10, min=0, max=100, unit="%"),),
        role="CONSTRAINT",
    ),
    FunctionSpec(
        id="portfolio.cash", name="현금 남기기", category="portfolio", category_label="포트폴리오 제약",
        summary="투자 비중 합계가 1에서 현금 비중을 뺀 값이 되도록 축소한다.",
        inputs=(_WEIGHT_IN,),
        outputs=(PortSpec("weight", "현금 반영 비중", "weight", "현금 비중을 남긴 종목 비중.", True),),
        params=(ParamSpec("cashWeightPct", "현금 비중", "number", "투자하지 않고 남길 비율.", 0, min=0, max=100, unit="%"),),
        role="CONSTRAINT",
    ),
    FunctionSpec(
        id="portfolio.risk-off", name="국면 따라 줄이기", category="portfolio", category_label="포트폴리오 제약",
        summary="조건이 거짓인 종목 비중을 줄여 현금으로 둔다. 시장 조건이면 전체에 같이 적용된다.",
        inputs=(
            _WEIGHT_IN,
            PortSpec("condition", "투자 조건", "condition", "보조 입력. 참이면 그대로, 거짓이면 줄인다. 예: 시장 > 시장 200일선.",
                     True, auxiliary=True),
        ),
        outputs=(PortSpec("weight", "조정된 비중", "weight", "줄어든 만큼은 현금이 된다.", True),),
        params=(ParamSpec("keepWeightPct", "유지 비율", "number", "조건이 거짓일 때 남길 비중 비율. 0이면 전부 현금.",
                          0, min=0, max=100, unit="%"),),
        role="CONSTRAINT",
    ),
    FunctionSpec(
        id="portfolio.target", name="목표 포트폴리오 만들기", category="portfolio", category_label="목표 포트폴리오",
        summary="배분 비중을 목표 비중과 현금으로 확정한다. 주문 수량은 만들지 않는다.",
        inputs=(PortSpec("weight", "배분 비중", "weight", "제약이 끝난 종목 비중.", True),),
        outputs=(PortSpec("portfolio", "목표 포트폴리오", "target_portfolio", "종목별 목표 비중과 현금 비중.", True),),
        params=(), role="TARGET",
    ),
)

_DECISION_AND_EXECUTION: tuple[FunctionSpec, ...] = (
    FunctionSpec(
        id="strategy.state", name="리밸런싱 하기", category="strategy", category_label="전략 판단",
        summary="목표 포트폴리오를 매 평가 시점의 목표 보유 상태로 바꾼다. 비중은 다시 계산하지 않는다.",
        inputs=(
            PortSpec("portfolio", "목표 포트폴리오", "target_portfolio",
                     "종목별 목표 비중. 목표에 없는 보유 종목은 목표 0.", True),
        ),
        outputs=(_RESULT_OUT,), params=(), role="STRATEGY", strategy_execution_type="STATE_REBALANCE",
    ),
    FunctionSpec(
        id="strategy.event", name="신호와 보유 비교하기", category="strategy", category_label="전략 판단",
        summary="진입·청산 계획과 현재 보유를 비교해 한 번의 전이만 발생시킨다. 청산이 진입보다 우선한다.",
        inputs=(PortSpec("plan", "진입·청산 계획", "event_plan", "최대 보유 수로 비중 정하기의 결과.", True),),
        outputs=(_RESULT_OUT,), params=(),
        role="STRATEGY", strategy_execution_type="EVENT_LIFECYCLE",
    ),
    FunctionSpec(
        id="execution.orders", name="주기·체결 방식 정하기", category="execution", category_label="실행",
        summary="6단계에서 정한 매매를 언제·어떻게 체결할지 정한다. 리밸런스 주기·날짜, 이탈 허용, 무거래 구간, 소수 주문, 현금이 모자랄 때의 처리. 체결은 항상 다음 거래일 시가다.",
        inputs=(PortSpec("result", "전략 판단", "strategy_result", "주문으로 바꿀 전략 판단 결과.", True),),
        outputs=(),
        params=(
            ParamSpec("rebalanceFrequency", "리밸런스", "enum",
                      "상태 평가형 전략이 비중을 다시 맞추는 주기. 맞추는 날은 리밸런스 날짜로 정한다. 이벤트형 전략은 이벤트가 날 때 주문한다.",
                      "monthly", options=_FREQUENCY),
            ParamSpec("rebalanceDay", "리밸런스 날짜", "enum",
                      "주기 안에서 비중을 맞추는 평가일. 첫 리밸런스는 항상 백테스트 첫 평가일이다.", "last",
                      options=_opts(("last", "주기의 마지막 평가일"), ("first", "주기의 첫 평가일"))),
            ParamSpec("noTradeBandPct", "무거래 구간", "number",
                      "리밸런스 때 이미 보유한 종목의 비중 차이가 이보다 작으면 사고팔지 않는다. 새로 사거나 전부 파는 매매는 항상 한다. 0이면 쓰지 않는다.",
                      0, min=0, max=100, unit="%p"),
            ParamSpec("driftThresholdPct", "이탈 허용", "number",
                      "주기가 아니어도 실제 비중이 목표에서 이만큼 벗어나면 맞춘다. 0이면 쓰지 않는다.",
                      0, min=0, max=100, unit="%p"),
            ParamSpec("allowFractional", "소수 주문", "boolean", "1주 미만 수량을 허용한다.", False),
            ParamSpec("shortfall", "현금 부족 시", "enum",
                      "다음 시가가 올라 현금이 모자랄 때. 줄이면 살 수 있는 만큼만 사고 나머지는 미체결로 남긴다.", "reduce",
                      options=_opts(("reduce", "수량 축소"), ("error", "오류"))),
        ),
        role="EXECUTION",
    ),
)

FUNCTIONS: tuple[FunctionSpec, ...] = (
    _UNIVERSE + _INDICATORS + _SCORES_AND_CONDITIONS + _LOGIC + _SELECTION_AND_EVENTS
    + _PORTFOLIO + _DECISION_AND_EXECUTION
)

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
        # 실제로 받는 타입. 비어 있지 않으면 type 대신 이것으로 연결을 판단한다.
        "accepts": list(port.accepts),
        "sameTypeAs": port.same_type_as,
        "mirrors": port.mirrors,
        "auxiliary": port.auxiliary,
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
    """편집기가 읽는 전체 정의. 단계 띠, 블록, 프리셋, 예제 전략, 공통 실행 조건.
    인스턴스 JSON은 캔버스에 놓을 때의 기본값이다. 연결 검증은 Python(n13)이 최종 판단한다."""
    from ai_studio.n12_strategies import example_catalog, preset_catalog  # n12는 n02만 보므로 순환하지 않는다.

    functions = []
    for fn in FUNCTIONS:
        params = {param.key: param.default for param in fn.params if param.default is not None}
        functions.append({
            "id": fn.id,
            "name": fn.name,
            "category": fn.category,
            "categoryLabel": fn.category_label,
            "summary": fn.summary,
            "role": fn.role,
            "stage": stage_of(fn.role),
            "version": fn.version,
            "needsPrevious": fn.needs_previous,
            "minimumHistory": fn.minimum_history(),
            "inputs": [_port_dict(port) for port in fn.inputs],
            "outputs": [_port_dict(port) for port in fn.outputs],
            "params": [_param_dict(param) for param in fn.params],
            "instance": {
                "id": instance_id(fn.id),
                "function_id": fn.id,
                "params": params,
            },
        })
    return {
        "stages": [
            {
                "id": stage, "label": label, "roles": list(roles),
                "lanes": [{"label": lane, "roles": list(lane_roles)} for lane, lane_roles in LANES.get(stage, ())],
            }
            for stage, label, roles in STAGES
        ],
        "functions": functions,
        "presets": preset_catalog(),
        "examples": example_catalog(),
        "runContext": [_param_dict(param) for param in RUN_CONTEXT],
    }
