"""블록 사이로 오가는 값의 종류. 화면의 연결선은 이 타입이 맞을 때만 허용한다."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

PortType = Literal[
    "universe", "condition", "event", "indicator", "score", "selection",
    "weight", "event_plan", "target_portfolio", "strategy_result",
]
ParamType = Literal["number", "integer", "enum", "boolean", "date"]
FunctionCategory = Literal[
    "universe", "momentum", "allocation", "factor", "logic", "strategy",
    "indicator", "condition", "selection", "portfolio", "execution", "score", "event",
]
ExecutionType = Literal[
    "STATE_REBALANCE", "EVENT_LIFECYCLE", "HYBRID", "MULTI_LEG_EVENT",
    "EXECUTION_ALGORITHM", "ANALYSIS_FUNCTION",
]
ParamValue = str | int | float | bool

EXECUTION_TYPE_METADATA: dict[str, dict[str, str]] = {
    "STATE_REBALANCE": {"ko_name": "상태 평가·리밸런싱형", "behavior_type": "일반조건형"},
    "EVENT_LIFECYCLE": {"ko_name": "이벤트·포지션 생애주기형", "behavior_type": "이벤트형"},
    "HYBRID": {"ko_name": "조건·이벤트 혼합형", "behavior_type": "혼합형"},
    "MULTI_LEG_EVENT": {"ko_name": "다중 포지션 이벤트형", "behavior_type": "이벤트형"},
    "EXECUTION_ALGORITHM": {"ko_name": "주문 집행 알고리즘", "behavior_type": "집행형"},
    "ANALYSIS_FUNCTION": {"ko_name": "분석·지원 기능", "behavior_type": "분석형"},
}

# 캔버스의 단계 띠. 블록은 role로 자기 단계가 정해진다. 팩터·시그널 갈래가 같은 띠를 공유한다.
STAGES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("universe", "종목 고르기", ("UNIVERSE",)),
    ("indicator", "지표 계산하기", ("INDICATOR",)),
    ("judge", "평가하기", ("SCORE", "CONDITION", "LOGIC")),
    ("pick", "매매 대상 정하기 | 매매 신호 잡기", ("SELECTION", "EVENT")),
    ("weight", "비중 정하기", ("WEIGHTING", "CONSTRAINT", "TARGET")),
    ("decide", "매매 결정하기", ("STRATEGY",)),
    ("execute", "주문하기", ("EXECUTION",)),
)


# 한 단계 안의 칸. 4단계는 상태형 갈래(대상)와 이벤트형 갈래(신호)가 왼쪽·오른쪽 칸에 나란히 놓인다.
LANES: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "pick": (("매매 대상 정하기", ("SELECTION",)), ("매매 신호 잡기", ("EVENT",))),
}


def stage_of(role: str) -> str:
    for stage, _, roles in STAGES:
        if role in roles:
            return stage
    raise ValueError(f"단계가 없는 역할입니다: {role}")


PORT_TYPE_LABEL: dict[str, str] = {
    "universe": "종목 집합",
    "condition": "상태 조건",
    "event": "발생 시점 이벤트",
    "indicator": "지표 값",
    "score": "종목 점수",
    "selection": "선정 종목",
    "weight": "배분 비중",
    "event_plan": "진입·청산 계획",
    "target_portfolio": "목표 포트폴리오",
    "strategy_result": "전략 판단 결과",
}


@dataclass(frozen=True, slots=True)
class RequiredData:
    fields: tuple[str, ...] = ()
    minimum_history: int = 0
    frequency: str | None = None

    def __post_init__(self) -> None:
        if self.minimum_history < 0:
            raise ValueError("minimum_history는 음수일 수 없습니다.")


@dataclass(frozen=True, slots=True)
class ParamOption:
    value: str
    label: str


@dataclass(frozen=True, slots=True)
class ParamSpec:
    key: str
    label: str
    type: ParamType
    description: str
    default: ParamValue | None = None
    options: tuple[ParamOption, ...] = ()
    unit: str | None = None
    min: float | None = None
    max: float | None = None


@dataclass(frozen=True, slots=True)
class PortSpec:
    key: str
    label: str
    type: PortType
    description: str
    required: bool
    # 팔레트에 보여줄 대표 타입은 type. accepts가 있으면 실제 허용 타입은 accepts를 따른다.
    accepts: tuple[PortType, ...] = ()
    multiple: bool = False
    min_connections: int | None = None
    same_type_as: str | None = None
    mirrors: str | None = None
    # 보조 입력. 주 흐름은 같은 단계나 바로 다음 단계에서만 받지만, 보조 입력은 앞 단계 어디서든 받는다.
    # 예: 국면 전환의 시장 조건(평가하기 → 비중 정하기), 역변동성 비중의 변동성(지표 → 비중).
    auxiliary: bool = False


@dataclass(frozen=True, slots=True)
class FunctionSpec:
    id: str
    name: str
    category: FunctionCategory
    category_label: str
    summary: str
    inputs: tuple[PortSpec, ...]
    outputs: tuple[PortSpec, ...]
    params: tuple[ParamSpec, ...]
    version: str = "1.0.0"
    role: Literal[
        "UNIVERSE", "INDICATOR", "CONDITION", "EVENT", "LOGIC", "SCORE",
        "SELECTION", "WEIGHTING", "CONSTRAINT", "TARGET", "STRATEGY", "EXECUTION",
    ] = "CONDITION"
    strategy_execution_type: ExecutionType | None = None
    required_data: RequiredData = RequiredData()
    input_schema_version: str = "1.0.0"
    output_schema_version: str = "1.0.0"
    # 직전 거래일의 입력도 필요한 블록. 실행기가 상류 그래프를 전날 기준으로 한 번 더 실행해 넘긴다.
    needs_previous: bool = False
    # 파라미터로 정해지는 최소 봉 수. 없으면 required_data.minimum_history를 쓴다.
    lookback: Callable[[Mapping[str, object]], int] | None = None

    def minimum_history(self, params: Mapping[str, object] | None = None) -> int:
        if self.lookback is None:
            return self.required_data.minimum_history
        merged = {param.key: param.default for param in self.params} | dict(params or {})
        return self.lookback(merged)

    @property
    def behavior_type(self) -> str | None:
        if self.strategy_execution_type is None:
            return None
        return EXECUTION_TYPE_METADATA[self.strategy_execution_type]["behavior_type"]


@dataclass(frozen=True, slots=True)
class StrategyNode:
    id: str
    function_id: str
    params: dict[str, ParamValue]


@dataclass(frozen=True, slots=True)
class StrategyEdge:
    from_node: str
    from_port: str
    to_node: str
    to_port: str


@dataclass(frozen=True, slots=True)
class StrategyGraph:
    nodes: tuple[StrategyNode, ...]
    edges: tuple[StrategyEdge, ...]


def _options(*pairs: tuple[str, str]) -> tuple[ParamOption, ...]:
    return tuple(ParamOption(value, label) for value, label in pairs)


# 전략 조립 화면 하단의 공통 실행 조건. 개별 함수 블록이 아니다.
# 리밸런스 주기는 실행 노드(execution.orders)가, 룩백은 각 지표 블록이 정한다.
RUN_CONTEXT: tuple[ParamSpec, ...] = (
    ParamSpec("startDate", "조회 시작일", "date", "백테스트 시작일.", default="2020-01-01"),
    ParamSpec("endDate", "조회 종료일", "date", "백테스트 종료일.", default="2025-12-31"),
    ParamSpec(
        "signalFrequency",
        "신호 확인",
        "enum",
        "그래프를 다시 평가하는 주기. 각 주기의 마지막 거래일 종가에 평가한다.",
        default="monthly",
        options=_options(("daily", "일간"), ("weekly", "주간"), ("monthly", "월간")),
    ),
    ParamSpec(
        "initialCapital",
        "초기 자금",
        "number",
        "백테스트 시작 시점의 투자 금액.",
        default=10000,
        min=0,
        unit="USD",
    ),
    ParamSpec(
        "currency",
        "통화",
        "enum",
        "초기 자금과 결과 금액의 통화.",
        default="USD",
        options=_options(("USD", "USD"), ("KRW", "KRW")),
    ),
    ParamSpec(
        "feeRate",
        "수수료",
        "number",
        "매매 금액 대비 수수료 비율.",
        default=0.1,
        min=0,
        unit="%",
    ),
    ParamSpec(
        "slippageRate",
        "슬리피지",
        "number",
        "체결 가격이 신호 가격에서 벗어나는 비율.",
        default=0.05,
        min=0,
        unit="%",
    ),
)


def accepted_types(port: PortSpec) -> tuple[PortType, ...]:
    return port.accepts or (port.type,)
