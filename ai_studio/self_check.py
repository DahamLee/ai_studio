from ai_studio.connect import can_connect, validate_graph
from ai_studio.functions import FUNCTIONS, get_function
from ai_studio.schema import StrategyEdge, StrategyGraph, StrategyNode


def main() -> None:
    ids = [fn.id for fn in FUNCTIONS]
    if len(ids) != 25:
        raise SystemExit(f"함수는 25개여야 합니다. 현재 {len(ids)}개")
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
                StrategyNode("m", "momentum.factor", {}),
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

    conditions = validate_graph(
        StrategyGraph(
            nodes=(
                StrategyNode("u", "universe.compose", {"market": "US_EQUITY"}),
                StrategyNode("t", "momentum.trend", {}),
                StrategyNode("b", "momentum.breakout", {}),
                StrategyNode("and", "logic.combine", {"operator": "and"}),
            ),
            edges=(
                StrategyEdge("u", "universe", "t", "universe"),
                StrategyEdge("u", "universe", "b", "universe"),
                StrategyEdge("t", "signal", "and", "signals"),
                StrategyEdge("b", "signal", "and", "signals"),
            ),
        )
    )
    if not conditions.ok:
        raise SystemExit(conditions.reason)

    pipeline = validate_graph(
        StrategyGraph(
            nodes=(
                StrategyNode("u", "universe.compose", {"market": "US_EQUITY"}),
                StrategyNode("score", "momentum.factor", {}),
                StrategyNode("pick", "selection.top-n", {"topN": 20}),
                StrategyNode("weight", "allocation.target-weight", {"scheme": "equal"}),
                StrategyNode("cap", "portfolio.max-weight", {"maxWeightPct": 10}),
                StrategyNode("cash", "portfolio.cash", {"cashWeightPct": 5}),
                StrategyNode("target", "portfolio.target", {}),
            ),
            edges=(
                StrategyEdge("u", "universe", "score", "universe"),
                StrategyEdge("score", "score", "pick", "score"),
                StrategyEdge("pick", "selection", "weight", "selection"),
                StrategyEdge("weight", "weight", "cap", "weight"),
                StrategyEdge("cap", "weight", "cash", "weight"),
                StrategyEdge("cash", "weight", "target", "weight"),
            ),
        )
    )
    if not pipeline.ok:
        raise SystemExit(pipeline.reason)

    signal = get_function("logic.combine").outputs[0]
    weighting = get_function("allocation.target-weight").inputs[0]
    if can_connect(signal, weighting).ok:
        raise SystemExit("조건 신호는 비중 블록에 바로 연결되면 안 됩니다.")
    rsi = get_function("indicator.rsi").outputs[0]
    threshold = get_function("condition.threshold").inputs[0]
    if not can_connect(rsi, threshold).ok:
        raise SystemExit("RSI 지표는 임계 조건에 연결되어야 합니다.")
    if can_connect(momentum.outputs[0], threshold).ok:
        raise SystemExit("점수는 조건 비교 입력에 연결되면 안 됩니다.")

    if combine.inputs[0].min_connections != 2:
        raise SystemExit("복수 조건 조합은 신호 2개 이상을 요구해야 합니다.")

    event = get_function("momentum.ma-cross")
    strategy = get_function("strategy.event")
    if can_connect(compose.outputs[0], strategy.inputs[0]).ok is False:
        raise SystemExit("유니버스 출력은 전략 입력과 연결되어야 합니다.")
    if can_connect(momentum.outputs[0], strategy.inputs[1]).ok:
        raise SystemExit("점수는 이벤트 입력에 연결되면 안 됩니다.")
    if not can_connect(event.outputs[0], strategy.inputs[1]).ok:
        raise SystemExit("교차 이벤트를 이벤트 전략에 연결할 수 있어야 합니다.")

    print(f"ok {len(FUNCTIONS)} functions")


if __name__ == "__main__":
    main()
