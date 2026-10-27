"""카탈로그·레지스트리·프리셋·예제 전략이 서로 맞는지 빠르게 확인한다.

실행: python -m ai_studio.n16_self_check
"""

from ai_studio.n02_schema import StrategyEdge, StrategyGraph, StrategyNode
from ai_studio.n10_functions import FUNCTIONS, get_function
from ai_studio.n11_registry import registered_ids
from ai_studio.n12_strategies import EXAMPLES, PRESETS, momentum_rebalance, momentum_top10, place
from ai_studio.n13_connect import can_connect, validate_graph, validate_strategy


def _port(function_id: str, key: str, side: str = "outputs"):
    return next(port for port in getattr(get_function(function_id), side) if port.key == key)


def main() -> None:
    ids = [fn.id for fn in FUNCTIONS]
    if len(set(ids)) != len(ids):
        raise SystemExit("함수 id가 중복되었습니다.")
    if set(ids) != set(registered_ids()):
        raise SystemExit(f"카탈로그와 레지스트리가 다릅니다: {sorted(set(ids) ^ set(registered_ids()))}")
    if any(fn.role in ("ALLOCATION",) for fn in FUNCTIONS):
        raise SystemExit("ALLOCATION 역할은 WEIGHTING·CONSTRAINT로 나눠야 합니다.")

    for preset in PRESETS:
        placed = place(preset.id, "p", {key: ("u", "universe") for key, _, _ in preset.inputs})
        graph = StrategyGraph((StrategyNode("u", "universe.compose", {}),) + placed.nodes, placed.edges)
        check = validate_graph(graph)
        if not check.ok:
            raise SystemExit(f"프리셋 {preset.id}: {check.reason}")

    graphs = [("momentum_rebalance", momentum_rebalance()), ("momentum_top10", momentum_top10())]
    graphs += [(key, build()) for key, (_, build) in EXAMPLES.items()]
    for name, graph in graphs:
        check = validate_strategy(graph)
        if not check.ok or check.warnings:
            raise SystemExit(f"{name}: {check.reason or check.warnings}")

    rules = (
        (("indicator.rsi", "value"), ("condition.threshold", "value"), True),
        (("score.from-indicator", "score"), ("condition.threshold", "value"), False),
        (("score.from-indicator", "score"), ("universe.exclude", "universe"), False),
        (("logic.combine", "condition"), ("allocation.target-weight", "selection"), False),
        (("selection.condition", "selection"), ("strategy.event", "gate"), True),
        (("event.transition", "entry"), ("strategy.state", "portfolio"), False),
        (("portfolio.target", "portfolio"), ("strategy.state", "portfolio"), True),
        (("strategy.event", "result"), ("execution.orders", "result"), True),
    )
    for (source_fn, source_key), (target_fn, target_key), expected in rules:
        result = can_connect(_port(source_fn, source_key), _port(target_fn, target_key, "inputs"))
        if result.ok is not expected:
            raise SystemExit(f"{source_fn}.{source_key} → {target_fn}.{target_key} 연결 판정이 틀렸습니다.")

    mixed = validate_graph(StrategyGraph(
        (StrategyNode("u", "universe.compose", {}), StrategyNode("r", "indicator.return", {}),
         StrategyNode("s", "score.from-indicator", {}), StrategyNode("and", "logic.combine", {})),
        (StrategyEdge("u", "universe", "r", "universe"), StrategyEdge("r", "value", "s", "value"),
         StrategyEdge("s", "score", "and", "conditions"), StrategyEdge("s", "score", "and", "conditions")),
    ))
    if mixed.ok:
        raise SystemExit("점수를 조건 조합에 넣는 그래프는 거부해야 합니다.")

    print(f"ok {len(FUNCTIONS)} functions, {len(PRESETS)} presets, {len(EXAMPLES)} examples")


if __name__ == "__main__":
    main()
