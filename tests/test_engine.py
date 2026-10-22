import unittest
from datetime import date, timedelta
from pathlib import Path

from ai_studio.n08_blocks import history_from_bars
from ai_studio.n14_engine import ExecutionContext, execute
from ai_studio.n01_frames import TargetPortfolio, UniverseFrame, WeightFrame
from ai_studio.n03_market_data import DATA_PATH, DailyBar, load_ohlc
from ai_studio.n02_schema import StrategyEdge, StrategyGraph, StrategyNode
from ai_studio.n12_strategies import momentum_top10


def closes(symbol: str, values: list[float]) -> list[DailyBar]:
    start = date(2026, 1, 1)
    return [
        DailyBar((start + timedelta(days=index)).isoformat(), symbol, value, value, value, value, 1_000)
        for index, value in enumerate(values)
    ]


def pipeline(scheme: str, top_n: int) -> StrategyGraph:
    return StrategyGraph(
        nodes=(
            StrategyNode("universe", "universe.compose", {"market": "US_EQUITY", "benchmark": "SPX"}),
            StrategyNode("score", "momentum.factor", {"lookbackMonths": 1, "skipMonths": 0}),
            StrategyNode("pick", "selection.top-n", {"topN": top_n}),
            StrategyNode("weight", "allocation.target-weight", {"scheme": scheme}),
            StrategyNode("target", "portfolio.target", {}),
        ),
        edges=(
            StrategyEdge("universe", "universe", "score", "universe"),
            StrategyEdge("score", "score", "pick", "score"),
            StrategyEdge("pick", "selection", "weight", "selection"),
            StrategyEdge("weight", "weight", "target", "weight"),
        ),
    )


class EngineTests(unittest.TestCase):
    def setUp(self):
        history = history_from_bars(closes("A", [100] * 21 + [150]) + closes("B", [100] * 21 + [110]))
        self.context = ExecutionContext("2026-01-22", history)

    def test_instance_outputs_are_role_frames(self):
        result = execute(pipeline("equal", 1), self.context)
        universe = result.port("universe", "universe")
        target = result.port("target", "portfolio")
        self.assertIsInstance(universe, UniverseFrame)
        self.assertEqual(universe.symbols(), ("A", "B"))
        self.assertIsInstance(target, TargetPortfolio)
        self.assertEqual([row.instrument_id for row in target.positions], ["A"])
        self.assertAlmostEqual(target.positions[0].target_weight, 1)
        self.assertAlmostEqual(target.cash_weight, 0)

    def test_swapping_weight_instance_does_not_change_executor(self):
        result = execute(pipeline("score", 2), self.context)
        weights = result.port("weight", "weight")
        self.assertIsInstance(weights, WeightFrame)
        by_id = {row.instrument_id: row.weight for row in weights.rows}
        self.assertAlmostEqual(by_id["A"], 0.5 / 0.6)
        self.assertAlmostEqual(by_id["B"], 0.1 / 0.6)

    def test_fundamental_score_stays_uncomputed_on_prices(self):
        graph = StrategyGraph(
            nodes=(
                StrategyNode("universe", "universe.compose", {"market": "US_EQUITY", "benchmark": "SPX"}),
                StrategyNode("value", "factor.value", {}),
            ),
            edges=(StrategyEdge("universe", "universe", "value", "universe"),),
        )
        with self.assertRaisesRegex(ValueError, "재무"):
            execute(graph, self.context)

    def test_score_cannot_feed_weight_directly(self):
        graph = StrategyGraph(
            nodes=(
                StrategyNode("universe", "universe.compose", {}),
                StrategyNode("score", "momentum.factor", {}),
                StrategyNode("weight", "allocation.target-weight", {}),
            ),
            edges=(
                StrategyEdge("universe", "universe", "score", "universe"),
                StrategyEdge("score", "score", "weight", "selection"),
            ),
        )
        with self.assertRaises(ValueError):
            execute(graph, self.context)

    def test_cross_instance_calls_event_strategy(self):
        history = history_from_bars(closes("AAPL", [3, 2, 1, 1, 3, 4]))
        graph = StrategyGraph(
            nodes=(
                StrategyNode("universe", "universe.compose", {}),
                StrategyNode("cross", "momentum.ma-cross", {"fastDays": 2, "slowDays": 3}),
                StrategyNode("decision", "strategy.event", {}),
            ),
            edges=(
                StrategyEdge("universe", "universe", "cross", "universe"),
                StrategyEdge("universe", "universe", "decision", "universe"),
                StrategyEdge("cross", "entry", "decision", "entry"),
                StrategyEdge("cross", "exit", "decision", "exit"),
            ),
        )
        result = execute(graph, ExecutionContext("2026-01-05", history))
        signal = result.port("decision", "result").signals[0]
        self.assertEqual(signal.action, "ENTER")

    @unittest.skipUnless(Path(DATA_PATH).exists(), "일봉 CSV가 없습니다.")
    def test_saved_ohlc_runs_the_momentum_instance(self):
        history = history_from_bars(load_ohlc())
        as_of = max(bar.date for rows in history.values() for bar in rows)
        result = execute(momentum_top10(), ExecutionContext(as_of, history))
        target = result.port("target", "portfolio")
        self.assertEqual(len(target.positions), 10)
        self.assertAlmostEqual(target.cash_weight, 0.05)
        self.assertTrue(all(abs(row.target_weight - 0.095) < 1e-9 for row in target.positions))


if __name__ == "__main__":
    unittest.main()
