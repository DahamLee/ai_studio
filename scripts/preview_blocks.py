"""마지막 거래일의 블록 출력을 실행기로 계산해 찍는다.

실행: PYTHONPATH=. .venv/bin/python scripts/preview_blocks.py
"""

from ai_studio.n08_blocks import rebalance_weights
from ai_studio.n03_market_data import bars_until, history_from_bars, load_ohlc
from ai_studio.n14_engine import ExecutionContext, execute
from ai_studio.n01_frames import WeightFrame, WeightRow
from ai_studio.n02_schema import StrategyEdge, StrategyGraph, StrategyNode
from ai_studio.n12_strategies import momentum_top10


def _graph() -> StrategyGraph:
    base = momentum_top10()
    return StrategyGraph(
        base.nodes + (
            StrategyNode("trend", "momentum.trend", {"method": "price_above_ma", "slowDays": 60}),
            StrategyNode("breakout", "momentum.breakout", {"windowDays": 60}),
            StrategyNode("combine", "logic.combine", {"operator": "and"}),
            StrategyNode("rsi", "indicator.rsi", {"period": 14}),
            StrategyNode("threshold", "condition.threshold", {"operator": "lt", "threshold": 30}),
            StrategyNode("calm", "factor.low-volatility", {"metric": "stdev", "windowDays": 60}),
            StrategyNode("cross", "momentum.ma-cross", {"fastDays": 20, "slowDays": 60}),
            StrategyNode("stop", "allocation.stop", {"stopLossPct": 10, "takeProfitPct": 30}),
        ),
        base.edges + (
            StrategyEdge("filter", "universe", "trend", "universe"),
            StrategyEdge("filter", "universe", "breakout", "universe"),
            StrategyEdge("trend", "condition", "combine", "conditions"),
            StrategyEdge("breakout", "condition", "combine", "conditions"),
            StrategyEdge("filter", "universe", "rsi", "universe"),
            StrategyEdge("rsi", "value", "threshold", "value"),
            StrategyEdge("filter", "universe", "calm", "universe"),
            StrategyEdge("filter", "universe", "cross", "universe"),
            StrategyEdge("cash", "weight", "stop", "weight"),
        ),
    )


def _prices(history, as_of) -> tuple[dict[str, float], dict[str, float], dict[str, float]]:
    entries: dict[str, float] = {}
    lasts: dict[str, float] = {}
    peaks: dict[str, float] = {}
    for symbol, series in history.items():
        bars = bars_until(history, symbol, as_of)
        if len(bars) < 22:
            continue
        entries[symbol] = bars[-22].close
        lasts[symbol] = bars[-1].close
        peaks[symbol] = max(bar.high for bar in bars[-22:])
    return entries, lasts, peaks


def main() -> None:
    history = history_from_bars(load_ohlc())
    as_of = max(bar.timestamp for rows in history.values() for bar in rows)
    entries, lasts, peaks = _prices(history, as_of)
    result = execute(_graph(), ExecutionContext(as_of, history, entry_prices=entries, peak_prices=peaks))

    universe = result.port("universe", "universe")
    kept = result.port("filter", "universe")
    removed = [symbol for symbol in universe.symbols if symbol not in set(kept.symbols)]
    ranked = result.port("pick", "selection")
    trend = result.port("trend", "condition")
    breakout = result.port("breakout", "condition")
    both = result.port("combine", "condition")
    rsi_values = result.port("rsi", "value")
    oversold = result.port("threshold", "condition")
    calm = result.port("calm", "score")
    weights = result.port("cash", "weight")
    stopped = result.port("stop", "weight")
    target = result.port("target", "portfolio")

    print(f"기준일 {as_of}")
    print(f"universe.compose {len(universe.symbols)}")
    print(f"universe.exclude 상장 148일 이상, 거래정지 제외 → {len(kept.symbols)}  제외 {len(removed)} {removed[:8]}")
    print("momentum.factor 6개월, 최근 1개월 제외, 상위 10")
    for row in ranked.rows:
        print(f"  {row.rank:2} {row.instrument_id:6} {row.score:+.2%}")
    true_trend = sorted(row.instrument_id for row in trend.rows if row.matched)
    true_break = sorted(row.instrument_id for row in breakout.rows if row.matched)
    true_both = sorted(row.instrument_id for row in both.rows if row.matched)
    print(f"momentum.trend 종가>60일선 참 {len(true_trend)}  예 {true_trend[:8]}")
    print(f"momentum.breakout 60일 고점 돌파 참 {len(true_break)}  예 {true_break[:12]}")
    print(f"logic.combine AND 참 {len(true_both)}  {true_both[:12]}")
    lowest = sorted(rsi_values.rows, key=lambda row: row.value)[:10]
    print(f"indicator.rsi 하위 10, 30 미만 {sum(row.matched for row in oversold.rows)}종목")
    oversold_flags = oversold.matches()
    for row in lowest:
        print(f"  {row.instrument_id:6} {row.value:6.2f}  30미만={oversold_flags[row.instrument_id]}")
    calmest = sorted(calm.rows, key=lambda row: row.score, reverse=True)[:10]
    print("factor.low-volatility 60일 표준편차가 작은 10종목")
    for row in calmest:
        print(f"  {row.instrument_id:6} 일간표준편차 {row.raw_value:.2%}  점수 {row.score:.4f}")
    print("allocation.target-weight 동일비중 → 상한 15% → 현금 5%")
    for position in target.positions:
        print(f"  {position.instrument_id:6} {position.target_weight:.2%}")
    print(f"  현금 {target.cash_weight:.2%}")

    last_cross = [
        event
        for event in (*result.port("cross", "entry").rows, *result.port("cross", "exit").rows)
        if event.as_of == as_of
    ]
    print(f"momentum.ma-cross 기준일 이벤트 {len(last_cross)}")
    for event in last_cross[:15]:
        print(f"  {event.instrument_id:6} {'진입' if event.kind == 'entry' else '청산'}")

    print("allocation.stop 21거래일 전 종가를 진입가로, 손절 10% 익절 30%")
    after = {row.instrument_id: row.weight for row in stopped.rows}
    for before in weights.rows:
        change = lasts[before.instrument_id] / entries[before.instrument_id] - 1
        print(f"  {before.instrument_id:6} 진입대비 {change:+.2%}  비중 {before.weight:.2%} → {after[before.instrument_id]:.2%}")

    notionals = {row.instrument_id: 0.1 / entries[row.instrument_id] for row in ranked.rows}
    values = {symbol: shares * lasts[symbol] for symbol, shares in notionals.items()}
    gross = sum(values.values())
    actual = WeightFrame(tuple(
        WeightRow(symbol, value / gross * 0.95) for symbol, value in values.items()
    ))
    chosen = rebalance_weights(weights, actual, due=False, drift_threshold_pct=5)
    print(f"allocation.rebalance 월간이 아닌 날, 이탈 허용 5%p → {'목표 비중' if chosen == weights else '현재 비중'}")
    goal = {row.instrument_id: row.weight for row in weights.rows}
    for row in actual.rows:
        print(f"  {row.instrument_id:6} 현재 {row.weight:.2%}  목표 {goal[row.instrument_id]:.2%}  차이 {row.weight - goal[row.instrument_id]:+.2%}")


if __name__ == "__main__":
    main()
