from dataclasses import dataclass

from ai_studio.functions import get_function
from ai_studio.schema import (
    FunctionSpec,
    PortSpec,
    PortType,
    StrategyEdge,
    StrategyGraph,
    accepted_types,
)


@dataclass(frozen=True, slots=True)
class ConnectResult:
    ok: bool
    reason: str | None = None


def output_type(fn: FunctionSpec, port_key: str, resolved: PortType | None = None) -> PortType:
    port = _find_port(fn.outputs, port_key)
    if port.mirrors:
        if resolved is None:
            raise ValueError(f"{fn.name}의 {port.label} 타입은 연결 후에 정해집니다.")
        return resolved
    return port.type


def can_connect(
    source: PortSpec,
    target: PortSpec,
    source_type: PortType | None = None,
) -> ConnectResult:
    actual = source_type if source_type is not None else (None if source.mirrors else source.type)
    if actual is None:
        return ConnectResult(False, f"{source.label}의 출력 타입이 아직 정해지지 않았습니다.")
    accepts = accepted_types(target)
    if actual not in accepts:
        names = ", ".join(accepts)
        return ConnectResult(False, f"{target.label}은 {names}만 받을 수 있습니다.")
    return ConnectResult(True)


def validate_graph(graph: StrategyGraph) -> ConnectResult:
    nodes = {node.id: node for node in graph.nodes}
    incoming: dict[str, list[StrategyEdge]] = {}

    for edge in graph.edges:
        from_node = nodes.get(edge.from_node)
        to_node = nodes.get(edge.to_node)
        if from_node is None or to_node is None:
            return ConnectResult(False, "연결이 존재하지 않는 블록을 가리킵니다.")

        from_fn = get_function(from_node.function_id)
        to_fn = get_function(to_node.function_id)
        from_port = _find_port(from_fn.outputs, edge.from_port)
        to_port = _find_port(to_fn.inputs, edge.to_port)
        source_type = _resolve_output_type(graph, from_node.id, from_port, set())
        if source_type is None:
            return ConnectResult(
                False,
                f"{from_fn.name}의 {from_port.label} 타입이 아직 정해지지 않았습니다.",
            )
        result = can_connect(from_port, to_port, source_type)
        if not result.ok:
            return result

        key = f"{edge.to_node}:{edge.to_port}"
        incoming.setdefault(key, []).append(edge)

    for node in graph.nodes:
        fn = get_function(node.function_id)
        for port in fn.inputs:
            edges = incoming.get(f"{node.id}:{port.key}", [])
            if port.required and not edges:
                return ConnectResult(False, f"{fn.name}의 {port.label} 입력이 비어 있습니다.")
            if port.min_connections is not None and len(edges) < port.min_connections:
                return ConnectResult(
                    False,
                    f"{fn.name}의 {port.label}에는 {port.min_connections}개 이상 연결해야 합니다.",
                )
            if not port.multiple and len(edges) > 1:
                return ConnectResult(False, f"{fn.name}의 {port.label}에는 하나만 연결할 수 있습니다.")
            if port.same_type_as:
                sibling = incoming.get(f"{node.id}:{port.same_type_as}", [])
                if not sibling or not edges:
                    continue
                left = _resolve_edge_type(graph, sibling[0], set())
                right = _resolve_edge_type(graph, edges[0], set())
                if left != right:
                    return ConnectResult(
                        False,
                        f"{fn.name}의 {port.label}은 {port.same_type_as}와 같은 타입이어야 합니다.",
                    )

    return ConnectResult(True)


def _resolve_edge_type(
    graph: StrategyGraph,
    edge: StrategyEdge,
    seen: set[str],
) -> PortType | None:
    node = next((item for item in graph.nodes if item.id == edge.from_node), None)
    if node is None:
        return None
    fn = get_function(node.function_id)
    return _resolve_output_type(graph, node.id, _find_port(fn.outputs, edge.from_port), seen)


def _resolve_output_type(
    graph: StrategyGraph,
    node_id: str,
    port: PortSpec,
    seen: set[str],
) -> PortType | None:
    if not port.mirrors:
        return port.type
    visit = f"{node_id}:{port.key}"
    if visit in seen:
        return None
    seen.add(visit)
    edge = next(
        (item for item in graph.edges if item.to_node == node_id and item.to_port == port.mirrors),
        None,
    )
    if edge is None:
        return None
    return _resolve_edge_type(graph, edge, seen)


def _find_port(ports: tuple[PortSpec, ...], key: str) -> PortSpec:
    for port in ports:
        if port.key == key:
            return port
    raise KeyError(f"포트를 찾을 수 없습니다: {key}")
