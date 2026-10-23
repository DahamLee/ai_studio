from dataclasses import dataclass
from datetime import date
from math import isfinite

from ai_studio.n10_functions import get_function
from ai_studio.n02_schema import (
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
    warnings: tuple[str, ...] = ()


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
    if len(nodes) != len(graph.nodes):
        return ConnectResult(False, "블록 ID가 중복되었습니다.")
    if _has_cycle(graph):
        return ConnectResult(False, "블록 연결에 순환이 있습니다.")
    for node in graph.nodes:
        try:
            fn = get_function(node.function_id)
        except KeyError as error:
            return ConnectResult(False, str(error))
        params_result = validate_params(fn, node.params)
        if not params_result.ok:
            return params_result
    incoming: dict[str, list[StrategyEdge]] = {}

    for edge in graph.edges:
        from_node = nodes.get(edge.from_node)
        to_node = nodes.get(edge.to_node)
        if from_node is None or to_node is None:
            return ConnectResult(False, "연결이 존재하지 않는 블록을 가리킵니다.")

        try:
            from_fn = get_function(from_node.function_id)
            to_fn = get_function(to_node.function_id)
            from_port = _find_port(from_fn.outputs, edge.from_port)
            to_port = _find_port(to_fn.inputs, edge.to_port)
        except KeyError as error:
            return ConnectResult(False, str(error))
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


def validate_strategy(graph: StrategyGraph) -> ConnectResult:
    """백테스트·실행이 가능한 완성 그래프. 실행 노드가 정확히 하나이고 전략 판단이 연결돼야 한다.
    실행 노드에 닿지 않는 블록은 계산만 하고 결과에 쓰이지 않으므로 경고한다."""
    base = validate_graph(graph)
    if not base.ok:
        return base
    executions = [node for node in graph.nodes if get_function(node.function_id).role == "EXECUTION"]
    if len(executions) != 1:
        return ConnectResult(False, f"실행 노드가 하나 있어야 합니다. 현재 {len(executions)}개")
    parents: dict[str, set[str]] = {}
    for edge in graph.edges:
        parents.setdefault(edge.to_node, set()).add(edge.from_node)
    reached: set[str] = set()
    stack = [executions[0].id]
    while stack:
        node_id = stack.pop()
        if node_id not in reached:
            reached.add(node_id)
            stack.extend(parents.get(node_id, ()))
    unused = tuple(sorted(node.id for node in graph.nodes if node.id not in reached))
    return ConnectResult(True, warnings=tuple(f"{node_id}: 실행 노드에 닿지 않습니다." for node_id in unused))


def validate_params(fn: FunctionSpec, values: dict[str, object]) -> ConnectResult:
    specs = {param.key: param for param in fn.params}
    unknown = set(values) - set(specs)
    if unknown:
        return ConnectResult(False, f"{fn.name}: 알 수 없는 파라미터 {sorted(unknown)}")
    resolved = {key: values.get(key, spec.default) for key, spec in specs.items()}
    for key, spec in specs.items():
        value = resolved[key]
        if value is None:
            return ConnectResult(False, f"{fn.name}.{key}: 필수 값이 없습니다.")
        if spec.type == "boolean" and type(value) is not bool:
            return ConnectResult(False, f"{fn.name}.{key}: boolean이 필요합니다.")
        if spec.type == "integer" and type(value) is not int:
            return ConnectResult(False, f"{fn.name}.{key}: 정수가 필요합니다.")
        if spec.type == "number" and (type(value) not in (int, float) or not isfinite(value)):
            return ConnectResult(False, f"{fn.name}.{key}: 유한한 숫자가 필요합니다.")
        if spec.type == "enum" and value not in {option.value for option in spec.options}:
            return ConnectResult(False, f"{fn.name}.{key}: 허용되지 않은 옵션입니다.")
        if spec.type == "date":
            try:
                if not isinstance(value, str):
                    raise ValueError()
                date.fromisoformat(value)
            except ValueError:
                return ConnectResult(False, f"{fn.name}.{key}: YYYY-MM-DD 날짜가 필요합니다.")
        if spec.type in ("number", "integer"):
            if spec.min is not None and value < spec.min:
                return ConnectResult(False, f"{fn.name}.{key}: 최솟값 {spec.min} 미만입니다.")
            if spec.max is not None and value > spec.max:
                return ConnectResult(False, f"{fn.name}.{key}: 최댓값 {spec.max} 초과입니다.")
    return ConnectResult(True)


def _has_cycle(graph: StrategyGraph) -> bool:
    neighbors: dict[str, list[str]] = {node.id: [] for node in graph.nodes}
    for edge in graph.edges:
        neighbors.setdefault(edge.from_node, []).append(edge.to_node)
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> bool:
        if node_id in visiting:
            return True
        if node_id in visited:
            return False
        visiting.add(node_id)
        if any(visit(child) for child in neighbors.get(node_id, ())):
            return True
        visiting.remove(node_id)
        visited.add(node_id)
        return False

    return any(visit(node_id) for node_id in neighbors)


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


def check_editor_graph(data: dict) -> dict[str, object]:
    """편집기 그래프 JSON을 검사한다. ok는 연결 규칙, complete는 실행 노드까지 닿는 완성 그래프 여부다."""
    from ai_studio.n12_strategies import graph_from_dict

    try:
        graph = graph_from_dict(data)
    except (KeyError, TypeError, ValueError) as error:
        return {"ok": False, "reason": f"그래프 형식 오류: {error}", "complete": False, "warnings": []}
    base = validate_graph(graph)
    if not base.ok:
        return {"ok": False, "reason": base.reason, "complete": False, "warnings": []}
    full = validate_strategy(graph)
    return {"ok": True, "reason": None, "complete": full.ok, "completeReason": full.reason,
            "warnings": list(full.warnings)}

