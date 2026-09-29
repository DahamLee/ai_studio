from ai_studio.connect import can_connect, validate_graph
from ai_studio.functions import FUNCTIONS, get_function
from ai_studio.schema import StrategyEdge, StrategyGraph, StrategyNode


def main() -> None:
    ids = [fn.id for fn in FUNCTIONS]
    if len(ids) != 14:
        raise SystemExit(f"함수는 14개여야 합니다. 현재 {len(ids)}개")
    if len(set(ids)) != len(ids):
        raise SystemExit("함수 id가 중복되었습니다.")

    compose = get_function("universe.compose")
    exclude = get_function("universe.exclude")
    momentum = get_function("momentum.factor")
    combine = get_function("logic.combine")

    universe_link = can_connect(compose.outputs[0], exclude.inputs[0])
    if not universe_link.ok:
        raise SystemExit(universe_link.reason)

    score_into_universe = can_connect(momentum.outputs[0], exclude.inputs[0])
    if score_into_universe.ok:
        raise SystemExit("점수는 종목 집합 입력에 연결되면 안 됩니다.")

    mixed = validate_graph(
        StrategyGraph(
            nodes=(
                StrategyNode("u", "universe.compose", {"market": "US_EQUITY"}),
                StrategyNode("m", "momentum.factor", {"topN": 20}),
                StrategyNode("t", "momentum.trend", {}),
                StrategyNode("and", "logic.combine", {"operator": "and"}),
                StrategyNode("w", "allocation.target-weight", {"scheme": "equal"}),
            ),
            edges=(
                StrategyEdge("u", "universe", "m", "universe"),
                StrategyEdge("u", "universe", "t", "universe"),
                StrategyEdge("m", "score", "and", "signals"),
                StrategyEdge("t", "signal", "and", "signals"),
                StrategyEdge("and", "signal", "w", "selection"),
            ),
        )
    )
    if mixed.ok:
        raise SystemExit("점수와 신호를 AND로 합치는 그래프는 거부해야 합니다.")

    valid = validate_graph(
        StrategyGraph(
            nodes=(
                StrategyNode("u", "universe.compose", {"market": "US_EQUITY"}),
                StrategyNode("t", "momentum.trend", {}),
                StrategyNode("b", "momentum.breakout", {}),
                StrategyNode("and", "logic.combine", {"operator": "and"}),
                StrategyNode("w", "allocation.target-weight", {"scheme": "equal"}),
            ),
            edges=(
                StrategyEdge("u", "universe", "t", "universe"),
                StrategyEdge("u", "universe", "b", "universe"),
                StrategyEdge("t", "signal", "and", "signals"),
                StrategyEdge("b", "signal", "and", "signals"),
                StrategyEdge("and", "signal", "w", "selection"),
            ),
        )
    )
    if not valid.ok:
        raise SystemExit(valid.reason)

    if combine.inputs[0].min_connections != 2:
        raise SystemExit("복수 조건 조합은 신호 2개 이상을 요구해야 합니다.")

    print(f"ok {len(FUNCTIONS)} functions")


if __name__ == "__main__":
    main()
