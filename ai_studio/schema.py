"""블록 사이로 오가는 값의 종류. 화면의 연결선은 이 타입이 맞을 때만 허용한다."""

from dataclasses import dataclass
from typing import Literal

PortType = Literal["universe", "signal", "score", "weight"]
ParamType = Literal["number", "integer", "enum", "boolean", "date"]
FunctionCategory = Literal["universe", "momentum", "allocation", "factor", "logic"]
ParamValue = str | int | float | bool

PORT_TYPE_LABEL: dict[str, str] = {
    "universe": "종목 집합",
    "signal": "매수·매도 신호",
    "score": "종목 점수",
    "weight": "목표 비중",
}


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
RUN_CONTEXT: tuple[ParamSpec, ...] = (
    ParamSpec("startDate", "조회 시작일", "date", "백테스트 시작일.", default="2020-01-01"),
    ParamSpec("endDate", "조회 종료일", "date", "백테스트 종료일.", default="2025-12-31"),
    ParamSpec(
        "lookback",
        "룩백",
        "enum",
        "신호를 계산할 때 기본으로 되돌아보는 기간.",
        default="12M",
        options=_options(("3M", "3M"), ("6M", "6M"), ("12M", "12M"), ("24M", "24M")),
    ),
    ParamSpec(
        "signalFrequency",
        "신호 확인",
        "enum",
        "신호를 다시 계산하는 주기.",
        default="monthly",
        options=_options(("daily", "일간"), ("weekly", "주간"), ("monthly", "월간")),
    ),
    ParamSpec(
        "rebalanceFrequency",
        "리밸런스",
        "enum",
        "비중을 다시 맞추는 주기.",
        default="monthly",
        options=_options(
            ("weekly", "주간"),
            ("monthly", "월간"),
            ("quarterly", "분기"),
            ("semiannual", "반기"),
        ),
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
