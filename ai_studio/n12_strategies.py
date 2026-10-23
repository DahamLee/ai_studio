"""전략은 함수가 아니라 블록 인스턴스의 연결이다.

인스턴스는 구현체 id와 파라미터를 가진 노드다. 같은 구현체를 두 번 놓으면 인스턴스가 두 개다.
프리셋은 자주 쓰는 원자 블록 조합을 저장한 하위 그래프다. 블록이 아니므로 실행기는 프리셋을 모른다.
캔버스에 놓으면 안쪽 블록들이 그대로 펼쳐지고, 각 블록의 파라미터를 따로 고칠 수 있다.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from ai_studio.n02_schema import StrategyEdge, StrategyGraph, StrategyNode

TRADING_DAYS_PER_MONTH = 21


@dataclass(frozen=True, slots=True)
class Preset:
    id: str
    name: str
    summary: str
    nodes: tuple[StrategyNode, ...]
    edges: tuple[StrategyEdge, ...]
    # 바깥에서 연결할 입력: (노출 키, 안쪽 노드, 안쪽 포트). 같은 키가 여러 노드로 들어갈 수 있다.
    inputs: tuple[tuple[str, str, str], ...]
    # 바깥으로 내보낼 출력: (노출 키, 안쪽 노드, 안쪽 포트).
    outputs: tuple[tuple[str, str, str], ...]


@dataclass(frozen=True, slots=True)
class Placed:
    nodes: tuple[StrategyNode, ...]
    edges: tuple[StrategyEdge, ...]
    outputs: dict[str, tuple[str, str]]

    def out(self, key: str) -> tuple[str, str]:
        return self.outputs[key]


def _n(id: str, function_id: str, **params: object) -> StrategyNode:
    return StrategyNode(id, function_id, dict(params))  # type: ignore[arg-type]


def _e(from_node: str, from_port: str, to_node: str, to_port: str) -> StrategyEdge:
    return StrategyEdge(from_node, from_port, to_node, to_port)


PRESETS: tuple[Preset, ...] = (
    Preset(
        "momentum-score", "모멘텀 점수", "최근 1개월을 뺀 12개월 수익률을 점수로 쓴다.",
        (_n("ret", "indicator.return", lookbackDays=252, skipDays=21), _n("score", "score.from-indicator")),
        (_e("ret", "value", "score", "value"),),
        (("universe", "ret", "universe"),), (("score", "score", "score"),),
    ),
    Preset(
        "low-volatility-score", "저변동성 점수", "1년 일간 수익률 표준편차가 낮을수록 높은 점수.",
        (_n("vol", "indicator.volatility", windowDays=252), _n("score", "score.from-indicator", direction="lower")),
        (_e("vol", "value", "score", "value"),),
        (("universe", "vol", "universe"),), (("score", "score", "score"),),
    ),
    Preset(
        "value-score", "가치 점수", "PBR이 낮을수록 높은 백분위 점수. 재무 데이터가 필요하다.",
        (_n("pbr", "indicator.fundamental", field="pbr"), _n("score", "score.from-indicator", direction="lower", method="rank")),
        (_e("pbr", "value", "score", "value"),),
        (("universe", "pbr", "universe"),), (("score", "score", "score"),),
    ),
    Preset(
        "trend-filter", "추세 조건", "종가가 200일 이동평균보다 높으면 참.",
        (_n("price", "indicator.price"), _n("ma", "indicator.moving-average", windowDays=200),
         _n("cmp", "condition.compare", operator="gt")),
        (_e("price", "value", "cmp", "left"), _e("ma", "value", "cmp", "right")),
        (("universe", "price", "universe"), ("universe", "ma", "universe")), (("condition", "cmp", "condition"),),
    ),
    Preset(
        "breakout", "신고가 돌파", "종가가 직전 252일 고가 이상이면 참.",
        (_n("price", "indicator.price"), _n("high", "indicator.high", windowDays=252),
         _n("cmp", "condition.compare", operator="gte")),
        (_e("price", "value", "cmp", "left"), _e("high", "value", "cmp", "right")),
        (("universe", "price", "universe"), ("universe", "high", "universe")), (("condition", "cmp", "condition"),),
    ),
    Preset(
        "golden-cross", "골든크로스·데드크로스", "20일 평균이 60일 평균을 상향 돌파하면 진입, 하향 돌파하면 청산.",
        (_n("fast", "indicator.moving-average", windowDays=20), _n("slow", "indicator.moving-average", windowDays=60),
         _n("cmp", "condition.compare", operator="gt"), _n("edge", "event.transition")),
        (_e("fast", "value", "cmp", "left"), _e("slow", "value", "cmp", "right"), _e("cmp", "condition", "edge", "condition")),
        (("universe", "fast", "universe"), ("universe", "slow", "universe")),
        (("entry", "edge", "entry"), ("exit", "edge", "exit")),
    ),
    Preset(
        "market-regime", "시장 국면", "동일가중 시장 지수가 200일 이동평균보다 높으면 참. 국면 전환 제약의 조건으로 쓴다.",
        (_n("price", "indicator.price", scope="market"), _n("ma", "indicator.moving-average", windowDays=200, scope="market"),
         _n("cmp", "condition.compare", operator="gt")),
        (_e("price", "value", "cmp", "left"), _e("ma", "value", "cmp", "right")),
        (("universe", "price", "universe"), ("universe", "ma", "universe")), (("condition", "cmp", "condition"),),
    ),
    Preset(
        "rsi-oversold", "RSI 과매도", "14일 RSI가 30 미만이면 참.",
        (_n("rsi", "indicator.rsi", period=14), _n("low", "condition.threshold", operator="lt", threshold=30)),
        (_e("rsi", "value", "low", "value"),),
        (("universe", "rsi", "universe"),), (("condition", "low", "condition"),),
    ),
)

_PRESETS = {preset.id: preset for preset in PRESETS}


def get_preset(preset_id: str) -> Preset:
    try:
        return _PRESETS[preset_id]
    except KeyError as error:
        raise KeyError(f"알 수 없는 프리셋입니다: {preset_id}") from error


def place(
    preset_id: str, prefix: str, inputs: Mapping[str, tuple[str, str]],
    params: Mapping[str, Mapping[str, object]] | None = None,
) -> Placed:
    """프리셋을 prefix_안쪽id 노드로 펼친다. inputs는 {노출 키: (바깥 노드, 바깥 포트)},
    params는 {안쪽 노드 id: 덮어쓸 파라미터}."""
    preset = get_preset(preset_id)
    overrides = params or {}
    unknown = set(overrides) - {node.id for node in preset.nodes}
    if unknown:
        raise KeyError(f"{preset_id}: 프리셋에 없는 노드 {sorted(unknown)}")
    missing = {key for key, _, _ in preset.inputs} - set(inputs)
    if missing:
        raise KeyError(f"{preset_id}: 연결되지 않은 입력 {sorted(missing)}")
    name = lambda local: f"{prefix}_{local}"  # noqa: E731
    nodes = tuple(StrategyNode(name(node.id), node.function_id, {**node.params, **overrides.get(node.id, {})})
                  for node in preset.nodes)
    edges = tuple(StrategyEdge(name(e.from_node), e.from_port, name(e.to_node), e.to_port) for e in preset.edges)
    edges += tuple(StrategyEdge(*inputs[key], name(node), port) for key, node, port in preset.inputs)
    return Placed(nodes, edges, {key: (name(node), port) for key, node, port in preset.outputs})


def preset_catalog() -> list[dict[str, object]]:
    """편집기 팔레트용 프리셋 정의."""
    return [
        {
            "id": preset.id, "name": preset.name, "summary": preset.summary,
            "nodes": [{"id": node.id, "function_id": node.function_id, "params": node.params} for node in preset.nodes],
            "edges": [{"from": e.from_node, "fromPort": e.from_port, "to": e.to_node, "toPort": e.to_port}
                      for e in preset.edges],
            "inputs": [{"key": key, "node": node, "port": port} for key, node, port in preset.inputs],
            "outputs": [{"key": key, "node": node, "port": port} for key, node, port in preset.outputs],
        }
        for preset in PRESETS
    ]


def _state_tail(universe: tuple[str, str], weight: tuple[str, str], rebalance: str) -> tuple[tuple[StrategyNode, ...], tuple[StrategyEdge, ...]]:
    """비중 → 목표 → 상태 판단 → 실행."""
    return (
        (_n("target", "portfolio.target"), _n("decision", "strategy.state"),
         _n("orders", "execution.orders", rebalanceFrequency=rebalance)),
        (StrategyEdge(*weight, "target", "weight"), StrategyEdge(*universe, "decision", "universe"),
         _e("target", "portfolio", "decision", "portfolio"), _e("decision", "result", "orders", "result")),
    )


def momentum_rebalance(
    *,
    top_n: int = 10,
    lookback_months: int = 1,
    skip_months: int = 0,
    cash_weight_pct: float = 20,
    rebalance: str = "monthly",
) -> StrategyGraph:
    """기간과 상위 종목 수, 현금 비중만 다른 모멘텀 리밸런싱 그래프."""
    score = place("momentum-score", "mom", {"universe": ("universe", "universe")}, {"ret": {
        "lookbackDays": lookback_months * TRADING_DAYS_PER_MONTH, "skipDays": skip_months * TRADING_DAYS_PER_MONTH,
    }})
    tail_nodes, tail_edges = _state_tail(("universe", "universe"), ("cash", "weight"), rebalance)
    return StrategyGraph(
        (_n("universe", "universe.compose", market="US_EQUITY", benchmark="SPX"),) + score.nodes + (
            _n("pick", "selection.top-n", topN=top_n),
            _n("weight", "allocation.target-weight", scheme="equal"),
            _n("cash", "portfolio.cash", cashWeightPct=cash_weight_pct),
        ) + tail_nodes,
        score.edges + (
            StrategyEdge(*score.out("score"), "pick", "score"),
            _e("pick", "selection", "weight", "selection"),
            _e("weight", "weight", "cash", "weight"),
        ) + tail_edges,
    )


def momentum_top10() -> StrategyGraph:
    """S&P 500에서 최근 1개월을 뺀 6개월 모멘텀 상위 10종목을 동일 비중으로 담는다."""
    score = place("momentum-score", "mom", {"universe": ("filter", "universe")},
                  {"ret": {"lookbackDays": 6 * TRADING_DAYS_PER_MONTH, "skipDays": TRADING_DAYS_PER_MONTH}})
    tail_nodes, tail_edges = _state_tail(("filter", "universe"), ("cash", "weight"), "monthly")
    return StrategyGraph(
        (
            _n("universe", "universe.compose", market="US_EQUITY", benchmark="SPX"),
            _n("filter", "universe.exclude", minListingDays=148),
        ) + score.nodes + (
            _n("pick", "selection.top-n", topN=10),
            _n("weight", "allocation.target-weight", scheme="equal"),
            _n("cap", "portfolio.max-weight", maxWeightPct=15),
            _n("cash", "portfolio.cash", cashWeightPct=5),
        ) + tail_nodes,
        (_e("universe", "universe", "filter", "universe"),) + score.edges + (
            StrategyEdge(*score.out("score"), "pick", "score"),
            _e("pick", "selection", "weight", "selection"),
            _e("weight", "weight", "cap", "weight"),
            _e("cap", "weight", "cash", "weight"),
        ) + tail_edges,
    )
