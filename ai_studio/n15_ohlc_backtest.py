"""저장된 일봉으로 모멘텀 그래프를 날짜마다 실행하고, 다음 시가에 체결한다.

종목 선정과 비중은 실행기가 그래프에서 계산한다. 이 모듈은 주문과 체결 원장만 맡는다.
"""

from datetime import datetime

from ai_studio.n01_frames import MarketBar, UniverseFrame
from ai_studio.n03_market_data import history_from_bars, load_ohlc
from ai_studio.n09_backtest import BacktestResult, run_backtest
from ai_studio.n14_engine import ExecutionContext, execute
from ai_studio.n07_runtime import AssetSignal, StrategyResult
from ai_studio.n12_strategies import momentum_rebalance


def run_momentum_topn(
    bars: tuple[MarketBar, ...] | None = None,
    *,
    lookback: int = 20,
    top_n: int = 10,
    step: int = 21,
    cash_weight: float = 0.2,
    initial_cash: float = 1_000_000,
) -> BacktestResult:
    rows = load_ohlc() if bars is None else bars
    history = history_from_bars(rows)
    counts: dict[datetime, int] = {}
    for row in rows:
        counts[row.timestamp] = counts.get(row.timestamp, 0) + 1
    dates = [day for day, count in sorted(counts.items()) if count >= 450]
    lookback_months = max(1, round(lookback / 21))
    warmup = lookback_months * 21
    if len(dates) <= warmup + 1:
        raise ValueError("리밸런싱에 필요한 거래일이 부족합니다.")
    graph = momentum_rebalance(
        top_n=top_n,
        lookback_months=lookback_months,
        skip_months=0,
        cash_weight_pct=cash_weight * 100,
    )
    by_date = {
        symbol: {bar.timestamp: bar for bar in series}
        for symbol, series in history.items()
    }

    results: list[StrategyResult] = []
    reference_prices: dict[datetime, dict[str, float]] = {}
    next_bars: dict[datetime, dict[str, MarketBar]] = {}
    for index in range(warmup, len(dates) - 1, step):
        day = dates[index]
        nxt = dates[index + 1]
        executed = execute(graph, ExecutionContext(day, history))
        target = executed.port("target", "portfolio")
        score_of = {row.instrument_id: row.score for row in executed.port("score", "score").rows}
        weight_of = {row.instrument_id: row.target_weight for row in target.positions}
        tradable = [
            symbol for symbol in executed.port("universe", "universe").symbols
            if day in by_date[symbol] and nxt in by_date[symbol]
        ]
        signals = tuple(
            AssetSignal(
                symbol,
                eligible=symbol in weight_of,
                score=score_of.get(symbol),
                action="REBALANCE",
                desired_state="LONG" if symbol in weight_of else "FLAT",
                target_weight=weight_of.get(symbol, 0.0),
            )
            for symbol in tradable
        )
        results.append(StrategyResult(
            day, "momentum-top10", "STATE_REBALANCE",
            UniverseFrame(day, tuple(tradable)), signals,
            tuple((row.symbol, row.target_weight or 0.0) for row in signals),
        ))
        reference_prices[day] = {symbol: by_date[symbol][day].close for symbol in tradable}
        next_bars[day] = {symbol: by_date[symbol][nxt] for symbol in tradable}
    return run_backtest(results, reference_prices, next_bars, initial_cash=initial_cash)
