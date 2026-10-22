"""전략 그래프를 읽어 레지스트리에 등록된 함수를 호출한다.

실행 순서에 특정 전략은 없다. 노드에 적힌 구현체와 연결만 따른다.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from ai_studio.n13_connect import validate_graph
from ai_studio.n10_functions import get_function
from ai_studio.n03_market_data import DailyBar
from ai_studio.n11_registry import get_handler
from ai_studio.n07_runtime import PositionState
from ai_studio.n02_schema import FunctionSpec, StrategyGraph, StrategyNode
from ai_studio.n01_frames import WeightFrame


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    """블록 출력이 아닌 시세·보유 정보. 모든 구현체가 같은 컨텍스트를 본다."""

    as_of: str
    history: Mapping[str, Sequence[DailyBar]]
    entry_prices: Mapping[str, float] = field(default_factory=dict)
    peak_prices: Mapping[str, float] = field(default_factory=dict)
    actual_weights: WeightFrame | None = None
    rebalance_due: bool = False
    positions: Mapping[str, PositionState] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """노드 id → 출력 포트 → 값."""

    outputs: dict[str, dict[str, object]]

    def port(self, node_id: str, port: str) -> object:
        return self.outputs[node_id][port]


def execute(graph: StrategyGraph, context: ExecutionContext) -> ExecutionResult:
    check = validate_graph(graph)
    if not check.ok:
        raise ValueError(check.reason)
    outputs: dict[str, dict[str, object]] = {}
    for node in execution_order(graph):
        spec = get_function(node.function_id)
        produced = get_handler(node.function_id)(_inputs(graph, node, spec, outputs), _with_defaults(spec, node.params), context)
        expected = {port.key for port in spec.outputs}
        if set(produced) != expected:
            raise ValueError(f"{spec.id}: 출력 포트가 스펙과 다릅니다.")
        outputs[node.id] = produced
    return ExecutionResult(outputs)


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
