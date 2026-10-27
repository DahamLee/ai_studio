"""완성된 전략 그래프를 거래일마다 실행하고, 실행 노드의 정책으로 주문·체결한다.

평가일마다 현재 보유를 컨텍스트로 넘겨 그래프를 실행하고, 실행 노드로 들어오는 전략 판단을
원장(Ledger)에 넣는다. 특정 전략을 알지 못한다. 그래프가 판단을, 실행 노드가 매매 시점을 정한다.
"""

from bisect import bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime

from ai_studio.n01_frames import MarketBar
from ai_studio.n02_schema import RUN_CONTEXT, StrategyGraph
from ai_studio.n03_market_data import History, history_from_bars, load_ohlc, session_date
from ai_studio.n07_runtime import StrategyResult
from ai_studio.n09_backtest import BacktestResult, Ledger
from ai_studio.n10_functions import get_function
from ai_studio.n12_strategies import momentum_rebalance
from ai_studio.n13_connect import validate_strategy
from ai_studio.n14_engine import ExecutionContext, execute


@dataclass(frozen=True, slots=True)
class RunSettings:
    """화면 하단 공통 실행 조건. 비율은 소수(0.001 = 0.1%)다. 기간이 None이면 데이터 전체."""

    start: date | None = None
    end: date | None = None
    signal_frequency: str = "daily"
    initial_capital: float = 10_000
    fee_rate: float = 0.0
    slippage_rate: float = 0.0

    @classmethod
    def from_params(cls, values: Mapping[str, object]) -> "RunSettings":
        """RUN_CONTEXT 키(퍼센트 단위)로 만든다. 빠진 값은 RUN_CONTEXT 기본값이다."""
        merged = {spec.key: spec.default for spec in RUN_CONTEXT} | dict(values)
        return cls(
            start=date.fromisoformat(str(merged["startDate"])) if merged.get("startDate") else None,
            end=date.fromisoformat(str(merged["endDate"])) if merged.get("endDate") else None,
            signal_frequency=str(merged["signalFrequency"]),
            initial_capital=float(merged["initialCapital"]),  # type: ignore[arg-type]
            fee_rate=float(merged["feeRate"]) / 100,  # type: ignore[arg-type]
            slippage_rate=float(merged["slippageRate"]) / 100,  # type: ignore[arg-type]
        )


def period_key(day: date, frequency: str) -> tuple[int, ...]:
    if frequency == "daily":
        return (day.toordinal(),)
    if frequency == "weekly":
        year, week, _ = day.isocalendar()
        return (year, week)
    if frequency == "monthly":
        return (day.year, day.month)
    if frequency == "quarterly":
        return (day.year, (day.month - 1) // 3)
    if frequency == "semiannual":
        return (day.year, (day.month - 1) // 6)
    raise ValueError(f"지원하지 않는 주기입니다: {frequency}")


def period_ends(days: Sequence[datetime], frequency: str) -> tuple[datetime, ...]:
    """각 주기의 마지막 거래일. days는 시각순이어야 한다."""
    last: dict[tuple[int, ...], datetime] = {}
    for day in days:
        last[period_key(session_date(day), frequency)] = day
    return tuple(sorted(last.values()))


def run_graph_backtest(graph: StrategyGraph, history: History, settings: RunSettings = RunSettings()) -> BacktestResult:
    check = validate_strategy(graph)
    if not check.ok:
        raise ValueError(check.reason)
    execution = next(node for node in graph.nodes if get_function(node.function_id).role == "EXECUTION")
    spec = get_function(execution.function_id)
    params = {param.key: param.default for param in spec.params} | dict(execution.params)
    feed = next(edge for edge in graph.edges if edge.to_node == execution.id and edge.to_port == "result")

    days = tuple(sorted({
        bar.timestamp for series in history.values() for bar in series
        if (settings.start is None or session_date(bar.timestamp) >= settings.start)
        and (settings.end is None or session_date(bar.timestamp) <= settings.end)
    }))
    evaluations = period_ends(days, settings.signal_frequency)
    due = set(period_ends(evaluations, str(params["rebalanceFrequency"]))) | set(evaluations[:1])
    drift = float(params["driftThresholdPct"]) / 100 or None
    ledger = Ledger(settings.initial_capital, fee_rate=settings.fee_rate,
                    slippage_rate=settings.slippage_rate,
                    allow_fractional_shares=bool(params["allowFractional"]), shortfall=str(params["shortfall"]))
    stamps = {symbol: [bar.available_at for bar in series] for symbol, series in history.items()}

    evaluation_days = set(evaluations)
    for day in days:
        # 종가 평가가 먼저다. 평가일 판단의 체결은 다음 거래일 시가에 일어나 다음 날 종가 평가에 반영된다.
        held = {symbol for symbol, position in ledger.holdings.items() if position.quantity}
        ledger.mark(day, _closes(history, stamps, held, day))
        if day not in evaluation_days or day == days[-1]:
            continue  # 평가일이 아니거나, 다음 거래일이 없어 체결할 수 없다.
        outputs = execute(graph, ExecutionContext(day, history, positions=dict(ledger.holdings)))
        result = outputs.port(feed.from_node, feed.from_port)
        if not isinstance(result, StrategyResult):
            raise ValueError("실행 노드에는 전략 판단이 들어와야 합니다.")
        symbols = {row.symbol for row in result.signals} | held
        prices = _closes(history, stamps, symbols, day)
        next_bars: dict[str, MarketBar] = {}
        for symbol in symbols:
            series = history.get(symbol, ())
            index = bisect_right(stamps.get(symbol, []), day)
            if index < len(series) and series[index].open_at is not None:
                next_bars[symbol] = series[index]
        ledger.step(result, prices, next_bars, rebalance_due=day in due, drift_threshold=drift, mark=False)
    return ledger.result()


def _closes(history: History, stamps: Mapping[str, list[datetime]], symbols, day: datetime) -> dict[str, float]:
    """day까지 공개된 마지막 종가. 그날 봉이 없으면(거래정지 등) 이전 종가다."""
    prices: dict[str, float] = {}
    for symbol in symbols:
        index = bisect_right(stamps.get(symbol, []), day)
        if index:
            prices[symbol] = history[symbol][index - 1].close
    return prices


def run_momentum_topn(
    bars: tuple[MarketBar, ...] | None = None,
    *,
    lookback_months: int = 1,
    top_n: int = 10,
    cash_weight: float = 0.2,
    initial_cash: float = 1_000_000,
    rebalance: str = "monthly",
) -> BacktestResult:
    """수익률 상위 top_n 종목을 동일 비중으로 담고 매월 말 리밸런싱한다.
    체결은 다음 시가라서 가격이 오르면 현금이 모자랄 수 있어 현금 비중을 남긴다."""
    graph = momentum_rebalance(
        top_n=top_n, lookback_months=lookback_months, skip_months=0,
        cash_weight_pct=cash_weight * 100, rebalance=rebalance,
    )
    history = history_from_bars(load_ohlc() if bars is None else bars)
    return run_graph_backtest(graph, history, RunSettings(signal_frequency=rebalance, initial_capital=initial_cash))

