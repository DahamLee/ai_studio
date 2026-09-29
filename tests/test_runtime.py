import unittest
from datetime import datetime, timedelta, timezone

from ai_studio.connect import can_connect, validate_graph
from ai_studio.backtest import run_backtest
from ai_studio.functions import FUNCTIONS, get_function
from ai_studio.runtime import (
    MarketBar, OrderIntent, PositionState, StrategyValidationError, UniverseFrame,
    evaluate_events, evaluate_state, fill_at_next_open, generate_orders,
    moving_average_cross_events,
)
from ai_studio.schema import StrategyEdge, StrategyGraph, StrategyNode

T = datetime(2026, 1, 2, 16, tzinfo=timezone.utc)


def universe(*symbols):
    return UniverseFrame(T, symbols)


def bars(symbol, closes, offset=0):
    return [MarketBar(T + timedelta(days=i + offset), T + timedelta(days=i + offset),
                      symbol, value, value) for i, value in enumerate(closes)]


class ContractTests(unittest.TestCase):
    def test_registry_and_universe_ports(self):
        self.assertEqual(len(FUNCTIONS), len({f.id for f in FUNCTIONS}))
        source = get_function("universe.compose").outputs[0]
        for name in ("strategy.state", "strategy.event", "strategy.hybrid"):
            fn = get_function(name)
            self.assertTrue(can_connect(source, fn.inputs[0]).ok)
            self.assertEqual(fn.outputs[0].type, "strategy_result")

    def test_event_does_not_connect_to_state_condition(self):
        source = get_function("momentum.ma-cross").outputs[0]
        self.assertFalse(can_connect(source, get_function("strategy.state").inputs[1]).ok)
        self.assertTrue(can_connect(source, get_function("strategy.event").inputs[1]).ok)

    def test_graph_validates_both_event_ports(self):
        graph = StrategyGraph(
            (StrategyNode("u", "universe.compose", {}),
             StrategyNode("m", "momentum.ma-cross", {}),
             StrategyNode("s", "strategy.event", {})),
            (StrategyEdge("u", "universe", "m", "universe"),
             StrategyEdge("u", "universe", "s", "universe"),
             StrategyEdge("m", "entry", "s", "entry"),
             StrategyEdge("m", "exit", "s", "exit")),
        )
        self.assertTrue(validate_graph(graph).ok)

    def test_invalid_params_and_cycle(self):
        bad = StrategyGraph((StrategyNode("m", "momentum.ma-cross", {"fastDays": 60, "slowDays": 20}),), ())
        self.assertIn("단기", validate_graph(bad).reason)
        cyclic = StrategyGraph((StrategyNode("a", "universe.exclude", {}),
                                StrategyNode("b", "universe.exclude", {})),
                               (StrategyEdge("a", "universe", "b", "universe"),
                                StrategyEdge("b", "universe", "a", "universe")))
        self.assertIn("순환", validate_graph(cyclic).reason)


class SignalTests(unittest.TestCase):
    def test_golden_cross_is_one_day_only(self):
        events = moving_average_cross_events(bars("A", [3, 2, 1, 1, 3, 4]), 2, 3)
        self.assertEqual([(e.as_of, e.entry) for e in events], [(T + timedelta(days=4), True)])

    def test_death_cross_only_once(self):
        events = moving_average_cross_events(bars("A", [3, 2, 1, 1, 3, 4, 3, 1, .5]), 2, 3)
        self.assertEqual(sum(e.exit for e in events), 1)

    def test_symbols_are_not_mixed(self):
        data = bars("A", [3, 2, 1, 1, 3, 4]) + bars("B", [100] * 6)
        self.assertEqual({e.symbol for e in moving_average_cross_events(data, 2, 3)}, {"A"})

    def test_warmup_never_becomes_false_signal(self):
        self.assertEqual(moving_average_cross_events(bars("A", [3, 2, 1]), 2, 3), ())

    def test_duplicate_bars_rejected(self):
        data = bars("A", [1, 2, 3])
        with self.assertRaisesRegex(StrategyValidationError, "중복"):
            moving_average_cross_events(data + data[:1], 2, 3)

    def test_invalid_price_and_future_availability_rejected(self):
        with self.assertRaises(StrategyValidationError):
            MarketBar(T, T, "A", 0, 1)
        with self.assertRaisesRegex(StrategyValidationError, "공개시점"):
            moving_average_cross_events([MarketBar(T, T - timedelta(days=1), "A", 1, 1)], 2, 3)


class LifecycleTests(unittest.TestCase):
    def test_state_false_and_removed_holding_target_zero(self):
        result = evaluate_state("s", universe("A"), {"A": False},
                                positions={"B": PositionState("B", 2, 10)})
        self.assertEqual([(s.symbol, s.target_weight) for s in result.signals], [("A", 0), ("B", 0)])

    def test_empty_universe_is_valid(self):
        self.assertEqual(evaluate_state("s", universe(), {}).signals, ())

    def test_state_not_due_does_not_order(self):
        result = evaluate_state("s", universe("A"), {"A": True})
        self.assertEqual(generate_orders(result, {}, {"A": 10}, portfolio_value=100,
                                         cash=100, rebalance_due=False), ())

    def test_long_reentry_and_flat_exit_do_nothing(self):
        self.assertEqual(evaluate_events("s", universe("A"), {"A"}, set(),
                                        {"A": PositionState("A", 2, 10)}).signals[0].action, "HOLD")
        self.assertEqual(evaluate_events("s", universe("A"), set(), {"A"}, {}).signals[0].action, "HOLD")

    def test_exit_wins_conflict_for_long(self):
        row = evaluate_events("s", universe("A"), {"A"}, {"A"},
                              {"A": PositionState("A", 2, 10)}).signals[0]
        self.assertEqual((row.action, row.desired_state), ("EXIT", "FLAT"))

    def test_hybrid_eligibility_only_gates_entry(self):
        no_entry = evaluate_events("s", universe("A"), {"A"}, set(), {}, eligible={"A": False})
        exit_result = evaluate_events("s", universe("A"), set(), {"A"},
                                      {"A": PositionState("A", 1, 10)}, eligible={"A": False})
        self.assertEqual(no_entry.signals[0].action, "HOLD")
        self.assertEqual(exit_result.signals[0].action, "EXIT")

    def test_hold_price_change_never_rebalances(self):
        result = evaluate_events("s", universe("A"), set(), set(), {"A": PositionState("A", 3, 10)})
        self.assertEqual(generate_orders(result, {"A": PositionState("A", 3, 10)}, {"A": 100},
                                         portfolio_value=300, cash=0), ())

    def test_event_id_and_pending_prevent_duplicate_order(self):
        result = evaluate_events("s", universe("A"), {"A"}, set(), {}, entry_weight=1)
        first = generate_orders(result, {}, {"A": 10}, portfolio_value=100, cash=100)
        self.assertEqual(len(first), 1)
        self.assertEqual(generate_orders(result, {}, {"A": 10}, portfolio_value=100, cash=100,
                                         pending=first), ())
        self.assertEqual(generate_orders(result, {}, {"A": 10}, portfolio_value=100, cash=100,
                                         processed_event_ids=frozenset({first[0].event_id})), ())

    def test_whole_share_fee_budget_and_sell_limit(self):
        result = evaluate_state("s", universe("A"), {"A": True})
        order = generate_orders(result, {}, {"A": 30}, portfolio_value=100, cash=100,
                                fee_rate=.1)
        self.assertEqual(order[0].quantity, 3)
        self.assertLessEqual(order[0].quantity * 30 * 1.1, 100)
        exit_result = evaluate_events("s", universe("A"), set(), {"A"},
                                      {"A": PositionState("A", 2, 20)})
        sell = generate_orders(exit_result, {"A": PositionState("A", 2, 20)}, {"A": 30},
                               portfolio_value=60, cash=0)
        self.assertEqual(sell[0].quantity, 2)

    def test_signal_day_cannot_fill_same_day(self):
        order = OrderIntent("A", "BUY", 1, T)
        with self.assertRaisesRegex(StrategyValidationError, "신호 이후"):
            fill_at_next_open([order], {"A": MarketBar(T, T, "A", 10, 10, open_at=T)}, {}, 100)

    def test_next_open_fee_slippage_position_and_history(self):
        order = OrderIntent("A", "BUY", 2, T, "event-1")
        next_open = T + timedelta(days=1) - timedelta(hours=6, minutes=30)
        next_bar = MarketBar(T + timedelta(days=1), T + timedelta(days=1), "A", 10, 11,
                             open_at=next_open)
        fills, holdings, cash = fill_at_next_open([order], {"A": next_bar}, {}, 100,
                                                  fee_rate=.01, slippage_rate=.01)
        self.assertEqual(fills[0].signal_at, T)
        self.assertEqual(fills[0].filled_at, next_open)
        self.assertEqual(fills[0].event_id, "event-1")
        self.assertAlmostEqual(holdings["A"].average_entry_price, 10.1)
        self.assertAlmostEqual(cash, 100 - 2 * 10.1 * 1.01)

    def test_invalid_condition_schema_rejected(self):
        with self.assertRaisesRegex(StrategyValidationError, "bool"):
            evaluate_state("s", universe("A"), {"A": None})

    def test_four_histories_separate_signal_from_trade(self):
        result = evaluate_events("s", universe("A"), {"A"}, set(), {}, entry_weight=1)
        open_at = T + timedelta(days=1) - timedelta(hours=6, minutes=30)
        bar = MarketBar(T + timedelta(days=1), T + timedelta(days=1), "A", 10, 11,
                        open_at=open_at)
        output = run_backtest([result], {T: {"A": 10}}, {T: {"A": bar}}, initial_cash=100)
        self.assertEqual(len(output.signal_history), 1)
        self.assertEqual(len(output.trade_log), 1)
        self.assertEqual(output.trade_log[0].filled_at, open_at)
        self.assertEqual(output.position_history[0].quantity, 10)
        self.assertEqual(output.portfolio_history[0].total_value, 110)

    def test_signal_can_exist_without_a_trade(self):
        result = evaluate_events("s", universe("A"), {"A"}, set(), {}, entry_weight=1)
        output = run_backtest([result], {T: {"A": 10}}, {T: {}}, initial_cash=0)
        self.assertEqual(len(output.signal_history), 1)
        self.assertEqual(output.trade_log, ())


if __name__ == "__main__":
    unittest.main()
