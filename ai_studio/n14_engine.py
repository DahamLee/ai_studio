"""전략 그래프를 읽어 레지스트리에 등록된 함수를 호출한다.

실행 순서에 특정 전략은 없다. 노드에 적힌 구현체와 연결만 따른다.
"""

from collections.abc import Mapping
from bisect import bisect_left
from dataclasses import dataclass, field, replace
from datetime import datetime

from ai_studio.n13_connect import validate_graph
from ai_studio.n10_functions import get_function
from ai_studio.n03_market_data import Fundamentals, History
from ai_studio.n11_registry import get_handler
from ai_studio.n07_runtime import PositionState
from ai_studio.n02_schema import FunctionSpec, StrategyGraph, StrategyNode
from ai_studio.n01_frames import require_tz


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    """블록 출력이 아닌 시세·보유 정보. 모든 구현체가 같은 컨텍스트를 본다."""

    as_of: datetime
    history: History
    positions: Mapping[str, PositionState] = field(default_factory=dict)
    # needs_previous 블록에만 채워진다. 같은 입력 포트의 직전 거래일 값이다. 직전 거래일이 없으면 None.
    previous_inputs: Mapping[str, object] | None = None
    # 분기 재무 공시. 재무 블록이 있는 그래프에만 채운다(run_graph_backtest가 읽는다).
    fundamentals: Fundamentals | None = None

    def __post_init__(self) -> None:
        require_tz(self.as_of, "context.as_of")


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """노드 id → 출력 포트 → 값. warnings는 데이터가 부족해 빈 결과를 낸 블록 등이다."""

    outputs: dict[str, dict[str, object]]
    warnings: tuple[str, ...] = ()

    def port(self, node_id: str, port: str) -> object:
        return self.outputs[node_id][port]


def execute(graph: StrategyGraph, context: ExecutionContext) -> ExecutionResult:
    check = validate_graph(graph)
    if not check.ok:
        raise ValueError(check.reason)
    outputs: dict[str, dict[str, object]] = {}
    warnings: list[str] = []
    for node in execution_order(graph):
        spec = get_function(node.function_id)
        params = _with_defaults(spec, node.params)
        inputs = _inputs(graph, node, spec, outputs)
        node_context = context
        if spec.needs_previous:
            node_context = replace(context, previous_inputs=_previous_inputs(graph, node, spec, context))
        produced = get_handler(node.function_id)(inputs, params, node_context)
        expected = {port.key for port in spec.outputs}
        if set(produced) != expected:
            raise ValueError(f"{spec.id}: 출력 포트가 스펙과 다릅니다.")
        outputs[node.id] = produced
        warnings.extend(_empty_output_warnings(node, spec, params, inputs, produced))
    return ExecutionResult(outputs, tuple(warnings))


def previous_trading_time(context: ExecutionContext) -> datetime | None:
    """as_of 직전에 공개된 봉 중 가장 늦은 시각."""
    latest: datetime | None = None
    for series in context.history.values():
        index = bisect_left(series, context.as_of, key=lambda bar: bar.available_at)
        if index:
            stamp = series[index - 1].available_at
            if latest is None or stamp > latest:
                latest = stamp
    return latest


def _previous_inputs(
    graph: StrategyGraph, node: StrategyNode, spec: FunctionSpec, context: ExecutionContext,
) -> Mapping[str, object] | None:
    """노드의 상류 그래프를 직전 거래일 기준으로 다시 실행해 같은 입력을 만든다."""
    previous = previous_trading_time(context)
    if previous is None:
        return None
    parents: dict[str, set[str]] = {}
    for edge in graph.edges:
        parents.setdefault(edge.to_node, set()).add(edge.from_node)
    upstream: set[str] = set()
    stack = list(parents.get(node.id, ()))
    while stack:
        current = stack.pop()
        if current not in upstream:
            upstream.add(current)
            stack.extend(parents.get(current, ()))
    sub = StrategyGraph(
        tuple(item for item in graph.nodes if item.id in upstream),
        tuple(edge for edge in graph.edges if edge.from_node in upstream and edge.to_node in upstream),
    )
    earlier = execute(sub, replace(context, as_of=previous, previous_inputs=None))
    return _inputs(graph, node, spec, earlier.outputs)


def _empty_output_warnings(
    node: StrategyNode, spec: FunctionSpec, params: Mapping[str, object],
    inputs: Mapping[str, object], produced: Mapping[str, object],
) -> list[str]:
    if spec.role not in ("INDICATOR", "SCORE"):
        return []
    universe = inputs.get("universe")
    if universe is not None and not getattr(universe, "symbols", ()):
        return []
    if any(getattr(value, "rows", None) for value in produced.values()):
        return []
    need = spec.minimum_history(params)
    hint = f" 종목마다 최소 {need}개 봉이 필요합니다." if need else ""
    return [f"{node.id}: 결과가 비었습니다.{hint}"]


def execution_order(graph: StrategyGraph) -> tuple[StrategyNode, ...]:
    nodes = {node.id: node for node in graph.nodes}
    pending = {node.id: 0 for node in graph.nodes}
    followers: dict[str, list[str]] = {node.id: [] for node in graph.nodes}
    for edge in graph.edges:
        pending[edge.to_node] += 1
        followers[edge.from_node].append(edge.to_node)
    ready = sorted(node_id for node_id, count in pending.items() if count == 0)
    order: list[StrategyNode] = []
    while ready:
        node_id = ready.pop(0)
        order.append(nodes[node_id])
        for follower in followers[node_id]:
            pending[follower] -= 1
            if pending[follower] == 0:
                ready.append(follower)
        ready.sort()
    if len(order) != len(graph.nodes):
        raise ValueError("블록 연결에 순환이 있습니다.")
    return tuple(order)


def _inputs(
    graph: StrategyGraph,
    node: StrategyNode,
    spec: FunctionSpec,
    outputs: Mapping[str, Mapping[str, object]],
) -> dict[str, object]:
    grouped: dict[str, list[object]] = {port.key: [] for port in spec.inputs}
    ports = {port.key: port for port in spec.inputs}
    for edge in graph.edges:
        if edge.to_node != node.id:
            continue
        grouped[edge.to_port].append(outputs[edge.from_node][edge.from_port])
    incoming: dict[str, object] = {}
    for key, values in grouped.items():
        if ports[key].multiple:
            incoming[key] = values
        elif values:
            incoming[key] = values[0]
    return incoming


def _with_defaults(spec: FunctionSpec, values: Mapping[str, object]) -> dict[str, object]:
    merged: dict[str, object] = {
        param.key: param.default
        for param in spec.params
        if param.default is not None
    }
    merged.update(values)
    return merged
