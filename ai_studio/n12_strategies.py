"""전략은 함수가 아니라 블록 인스턴스의 연결이다.

인스턴스는 구현체 id와 파라미터를 가진 노드다. 같은 구현체를 두 번 놓으면 인스턴스가 두 개다.
"""

from ai_studio.n02_schema import StrategyEdge, StrategyGraph, StrategyNode


def momentum_rebalance(
    *,
    top_n: int = 10,
    lookback_months: int = 1,
    skip_months: int = 0,
    cash_weight_pct: float = 20,
    rebalance: str = "monthly",
) -> StrategyGraph:
    """기간과 상위 종목 수, 현금 비중만 다른 모멘텀 리밸런싱 그래프."""
    return StrategyGraph(
        nodes=(
            StrategyNode("universe", "universe.compose", {"market": "US_EQUITY", "benchmark": "SPX"}),
            StrategyNode("score", "momentum.factor", {"lookbackMonths": lookback_months, "skipMonths": skip_months}),
            StrategyNode("pick", "selection.top-n", {"topN": top_n}),
            StrategyNode("weight", "allocation.target-weight", {"scheme": "equal"}),
            StrategyNode("cash", "portfolio.cash", {"cashWeightPct": cash_weight_pct}),
            StrategyNode("target", "portfolio.target", {}),
            StrategyNode("decision", "strategy.state", {}),
            StrategyNode("orders", "execution.orders", {"rebalanceFrequency": rebalance}),
        ),
        edges=(
            StrategyEdge("universe", "universe", "score", "universe"),
            StrategyEdge("score", "score", "pick", "score"),
            StrategyEdge("pick", "selection", "weight", "selection"),
            StrategyEdge("weight", "weight", "cash", "weight"),
            StrategyEdge("cash", "weight", "target", "weight"),
            StrategyEdge("universe", "universe", "decision", "universe"),
            StrategyEdge("target", "portfolio", "decision", "portfolio"),
            StrategyEdge("decision", "result", "orders", "result"),
        ),
    )


def momentum_top10() -> StrategyGraph:
    """S&P 500에서 6개월 모멘텀 상위 10종목을 동일 비중으로 담는 인스턴스 묶음."""
    return StrategyGraph(
        nodes=(
            StrategyNode("universe", "universe.compose", {"market": "US_EQUITY", "benchmark": "SPX"}),
            StrategyNode("filter", "universe.exclude", {"minListingDays": 148}),
            StrategyNode("score", "momentum.factor", {"lookbackMonths": 6, "skipMonths": 1}),
            StrategyNode("pick", "selection.top-n", {"topN": 10}),
            StrategyNode("weight", "allocation.target-weight", {"scheme": "equal"}),
            StrategyNode("cap", "portfolio.max-weight", {"maxWeightPct": 15}),
            StrategyNode("cash", "portfolio.cash", {"cashWeightPct": 5}),
            StrategyNode("target", "portfolio.target", {}),
            StrategyNode("decision", "strategy.state", {}),
            StrategyNode("orders", "execution.orders", {"rebalanceFrequency": "monthly"}),
        ),
        edges=(
            StrategyEdge("universe", "universe", "filter", "universe"),
            StrategyEdge("filter", "universe", "score", "universe"),
            StrategyEdge("score", "score", "pick", "score"),
            StrategyEdge("pick", "selection", "weight", "selection"),
            StrategyEdge("weight", "weight", "cap", "weight"),
            StrategyEdge("cap", "weight", "cash", "weight"),
            StrategyEdge("cash", "weight", "target", "weight"),
            StrategyEdge("filter", "universe", "decision", "universe"),
            StrategyEdge("target", "portfolio", "decision", "portfolio"),
            StrategyEdge("decision", "result", "orders", "result"),
        ),
    )
