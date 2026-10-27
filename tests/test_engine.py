import unittest
from datetime import date, timedelta
from pathlib import Path

from ai_studio.n14_engine import ExecutionContext, execute
from ai_studio.n01_frames import TargetPortfolio, UniverseFrame, WeightFrame
from ai_studio.n03_market_data import DATA_PATH, daily_bar, history_from_bars, load_ohlc, session_close as at
from ai_studio.n01_frames import MarketBar
from ai_studio.n02_schema import StrategyEdge, StrategyGraph, StrategyNode
from ai_studio.n12_strategies import momentum_top10, place
from ai_studio.n13_connect import validate_strategy
from ai_studio.n07_runtime import evaluate_state
from ai_studio.n09_backtest import Ledger
from ai_studio.n15_ohlc_backtest import RunSettings, run_graph_backtest


def closes(symbol: str, values: list[float]) -> list[MarketBar]:
    start = date(2026, 1, 1)
    return [
        daily_bar(symbol, start + timedelta(days=index), value, value, value, value, 1_000)
        for index, value in enumerate(values)
    ]


def pipeline(scheme: str, top_n: int) -> StrategyGraph:
    return StrategyGraph(
        nodes=(
            StrategyNode("universe", "universe.compose", {"market": "US_EQUITY", "benchmark": "SPX"}),
            StrategyNode("ret", "indicator.return", {"lookbackDays": 21}),
            StrategyNode("score", "score.from-indicator", {}),
            StrategyNode("pick", "selection.top-n", {"topN": top_n}),
            StrategyNode("weight", "allocation.target-weight", {"scheme": scheme}),
            StrategyNode("target", "portfolio.target", {}),
        ),
        edges=(
            StrategyEdge("universe", "universe", "ret", "universe"),
            StrategyEdge("ret", "value", "score", "value"),
            StrategyEdge("score", "score", "pick", "score"),
            StrategyEdge("pick", "selection", "weight", "selection"),
            StrategyEdge("weight", "weight", "target", "weight"),
        ),
    )


class EngineTests(unittest.TestCase):
    def setUp(self):
        history = history_from_bars(closes("A", [100] * 21 + [150]) + closes("B", [100] * 21 + [110]))
        self.context = ExecutionContext(at("2026-01-22"), history)

    def test_instance_outputs_are_role_frames(self):
        result = execute(pipeline("equal", 1), self.context)
        universe = result.port("universe", "universe")
        target = result.port("target", "portfolio")
        self.assertIsInstance(universe, UniverseFrame)
        self.assertEqual(universe.symbols, ("A", "B"))
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
                StrategyNode("value", "indicator.fundamental", {"field": "pbr"}),
            ),
            edges=(StrategyEdge("universe", "universe", "value", "universe"),),
        )
        with self.assertRaisesRegex(ValueError, "재무"):
            execute(graph, self.context)

    def test_score_cannot_feed_weight_directly(self):
        graph = StrategyGraph(
            nodes=(
                StrategyNode("universe", "universe.compose", {}),
                StrategyNode("ret", "indicator.return", {}),
                StrategyNode("score", "score.from-indicator", {}),
                StrategyNode("weight", "allocation.target-weight", {}),
            ),
            edges=(
                StrategyEdge("universe", "universe", "ret", "universe"),
                StrategyEdge("ret", "value", "score", "value"),
                StrategyEdge("score", "score", "weight", "selection"),
            ),
        )
        with self.assertRaises(ValueError):
            execute(graph, self.context)

    def test_cross_preset_calls_event_strategy(self):
        history = history_from_bars(closes("AAPL", [3, 2, 1, 1, 3, 4]))
        cross = place("golden-cross", "cross", {"universe": ("universe", "universe")},
                      {"fast": {"windowDays": 2}, "slow": {"windowDays": 3}})
        graph = StrategyGraph(
            nodes=(StrategyNode("universe", "universe.compose", {}),) + cross.nodes
            + (StrategyNode("decision", "strategy.event", {}),),
            edges=cross.edges + (
                StrategyEdge("universe", "universe", "decision", "universe"),
                StrategyEdge(*cross.out("entry"), "decision", "entry"),
                StrategyEdge(*cross.out("exit"), "decision", "exit"),
            ),
        )
        result = execute(graph, ExecutionContext(at("2026-01-05"), history))
        signal = result.port("decision", "result").signals[0]
        self.assertEqual((signal.action, signal.target_weight), ("ENTER", 0.1))

    @unittest.skipUnless(Path(DATA_PATH).exists(), "일봉 CSV가 없습니다.")
    def test_saved_ohlc_runs_the_momentum_instance(self):
        history = history_from_bars(load_ohlc())
        as_of = max(bar.timestamp for rows in history.values() for bar in rows)
        result = execute(momentum_top10(), ExecutionContext(as_of, history))
        target = result.port("target", "portfolio")
        self.assertEqual(len(target.positions), 10)
        self.assertAlmostEqual(target.cash_weight, 0.05)
        self.assertTrue(all(abs(row.target_weight - 0.095) < 1e-9 for row in target.positions))


def momentum_strategy(*, drift: float = 0, rebalance: str = "monthly") -> StrategyGraph:
    base = pipeline("equal", 1)
    return StrategyGraph(
        base.nodes + (
            StrategyNode("decision", "strategy.state", {}),
            StrategyNode("orders", "execution.orders", {"rebalanceFrequency": rebalance, "driftThresholdPct": drift}),
        ),
        base.edges + (
            StrategyEdge("universe", "universe", "decision", "universe"),
            StrategyEdge("target", "portfolio", "decision", "portfolio"),
            StrategyEdge("decision", "result", "orders", "result"),
        ),
    )


class StrategyRunTests(unittest.TestCase):
    def setUp(self):
        # A는 21일 동안 오르고, B는 그대로다. 1월 말과 2월 말에 평가한다.
        self.history = history_from_bars(
            closes("A", [100 + index for index in range(60)]) + closes("B", [100] * 60)
        )

    def test_complete_graph_needs_one_execution_node(self):
        self.assertIn("실행 노드", validate_strategy(pipeline("equal", 1)).reason)
        graph = momentum_strategy()
        self.assertTrue(validate_strategy(graph).ok)
        extra = StrategyGraph(graph.nodes + (StrategyNode("rsi", "indicator.rsi", {}),),
                              graph.edges + (StrategyEdge("universe", "universe", "rsi", "universe"),))
        self.assertEqual(validate_strategy(extra).warnings, ("rsi: 실행 노드에 닿지 않습니다.",))

    def test_factor_graph_reaches_the_ledger(self):
        result = run_graph_backtest(momentum_strategy(), self.history,
                                    RunSettings(signal_frequency="daily", initial_capital=1_000))
        self.assertGreater(len(result.trade_log), 0)
        self.assertEqual({fill.symbol for fill in result.trade_log if fill.side == "BUY"}, {"A"})
        self.assertTrue(all(fill.filled_at > fill.signal_at for fill in result.trade_log))
        # 거래일마다 한 번씩 평가가 남는다.
        self.assertEqual(len(result.portfolio_history), 60)
        # 점수는 1월 22일부터 나오지만 월간 리밸런스라 1월 말에 처음 산다.
        self.assertEqual([(fill.signal_at.date(), fill.side) for fill in result.trade_log],
                         [(date(2026, 1, 31), "BUY")])

    def test_drift_rebalances_between_dates(self):
        quiet = run_graph_backtest(momentum_strategy(rebalance="semiannual"), self.history,
                                   RunSettings(signal_frequency="daily", initial_capital=1_000))
        drifted = run_graph_backtest(momentum_strategy(rebalance="semiannual", drift=0.1), self.history,
                                     RunSettings(signal_frequency="daily", initial_capital=1_000))
        self.assertGreater(len(drifted.trade_log), len(quiet.trade_log))

    def test_unlisted_symbol_is_not_in_the_universe(self):
        history = history_from_bars(closes("A", [100] * 5) + closes("LATE", [100] * 5)[3:])
        context = ExecutionContext(at("2026-01-02"), history)
        graph = StrategyGraph((StrategyNode("universe", "universe.compose", {}),), ())
        self.assertEqual(execute(graph, context).port("universe", "universe").symbols, ("A",))

    def test_order_without_next_bar_is_skipped(self):
        result = evaluate_state("s", UniverseFrame(at("2026-01-02"), ("A",)), {"A": 1.0})
        ledger = Ledger(100)
        ledger.step(result, {"A": 10}, {})
        self.assertEqual(ledger.result().trade_log, ())
        self.assertEqual([order.symbol for order in ledger.result().skipped_orders], ["A"])


if __name__ == "__main__":
    unittest.main()
