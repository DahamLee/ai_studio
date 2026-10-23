import unittest
from datetime import datetime, timedelta, timezone
from tempfile import TemporaryDirectory

from ai_studio.n13_connect import can_connect, validate_graph
from ai_studio.n09_backtest import run_backtest, trade_rows, write_trades_csv
from ai_studio.n10_functions import FUNCTIONS, function_catalog, get_function
from ai_studio.n01_frames import MarketBar, StrategyValidationError, UniverseFrame
from ai_studio.n07_runtime import (
    OrderIntent, PositionState,
    evaluate_events, evaluate_state, execution_open, fill_at_next_open, generate_orders,
    trade_notional,
)
from ai_studio.n05_signals import moving_average_cross_events
from ai_studio.n02_schema import StrategyEdge, StrategyGraph, StrategyNode

T = datetime(2026, 1, 2, 16, tzinfo=timezone.utc)


def universe(*symbols):
    return UniverseFrame(T, symbols)


def bars(symbol, closes, offset=0):
    return [MarketBar(symbol, T + timedelta(days=i + offset), value, value, value, value)
            for i, value in enumerate(closes)]


class ContractTests(unittest.TestCase):
    def test_catalog_instance_keeps_defaults_only(self):
        catalog = function_catalog()
        functions = catalog["functions"]
        self.assertEqual(len(functions), len(FUNCTIONS))
        compose = next(fn for fn in functions if fn["id"] == "universe.compose")
        self.assertEqual(compose["instance"]["id"], "universe_compose")
        self.assertEqual(compose["instance"]["function_id"], "universe.compose")
        self.assertEqual(
            compose["instance"]["params"],
            {"market": "US_EQUITY", "benchmark": "SPX", "minMarketCap": 0},
        )
        state = next(fn for fn in functions if fn["id"] == "strategy.state")
        self.assertEqual(state["instance"]["params"], {})

    def test_registry_and_universe_ports(self):
        self.assertEqual(len(FUNCTIONS), len({f.id for f in FUNCTIONS}))
        source = get_function("universe.compose").outputs[0]
        for name in ("strategy.state", "strategy.event", "strategy.hybrid"):
            fn = get_function(name)
            self.assertTrue(can_connect(source, fn.inputs[0]).ok)
            self.assertEqual(fn.outputs[0].type, "strategy_result")

    def test_event_does_not_connect_to_state_portfolio(self):
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
        self.assertEqual([(e.as_of, e.kind) for e in events.rows], [(T + timedelta(days=4), "entry")])

    def test_death_cross_only_once(self):
        events = moving_average_cross_events(bars("A", [3, 2, 1, 1, 3, 4, 3, 1, .5]), 2, 3)
        self.assertEqual(sum(e.kind == "exit" for e in events.rows), 1)

    def test_symbols_are_not_mixed(self):
        data = bars("A", [3, 2, 1, 1, 3, 4]) + bars("B", [100] * 6)
        self.assertEqual({e.instrument_id for e in moving_average_cross_events(data, 2, 3).rows}, {"A"})

    def test_warmup_never_becomes_false_signal(self):
        self.assertEqual(moving_average_cross_events(bars("A", [3, 2, 1]), 2, 3).rows, ())

    def test_duplicate_bars_rejected(self):
        data = bars("A", [1, 2, 3])
        with self.assertRaisesRegex(StrategyValidationError, "중복"):
            moving_average_cross_events(data + data[:1], 2, 3)

    def test_invalid_price_and_future_availability_rejected(self):
        with self.assertRaises(StrategyValidationError):
            MarketBar("A", T, 0, 1, 1, 1)
        with self.assertRaisesRegex(StrategyValidationError, "공개시점"):
            moving_average_cross_events([MarketBar("A", T, 1, 1, 1, 1, available_at=T - timedelta(days=1))], 2, 3)


class LifecycleTests(unittest.TestCase):
    def test_state_false_and_removed_holding_target_zero(self):
        result = evaluate_state("s", universe("A"), {},
                                positions={"B": PositionState("B", 2, 10)})
        self.assertEqual([(s.symbol, s.target_weight) for s in result.signals], [("A", 0), ("B", 0)])

    def test_empty_universe_is_valid(self):
        self.assertEqual(evaluate_state("s", universe(), {}).signals, ())

    def test_state_not_due_does_not_order(self):
        result = evaluate_state("s", universe("A"), {"A": 1.0})
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
        result = evaluate_state("s", universe("A"), {"A": 1.0})
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
            fill_at_next_open([order], {"A": MarketBar("A", T, 10, 10, 10, 10, open_at=T)}, {}, 100)

    def test_next_open_fee_slippage_position_and_history(self):
        order = OrderIntent("A", "BUY", 2, T, "event-1")
        next_open = T + timedelta(days=1) - timedelta(hours=6, minutes=30)
        next_bar = MarketBar("A", T + timedelta(days=1), 10, 11, 10, 11, open_at=next_open)
        fills, holdings, cash = fill_at_next_open([order], {"A": next_bar}, {}, 100,
                                                  fee_rate=.01, slippage_rate=.01)
        self.assertEqual(fills[0].signal_at, T)
        self.assertEqual(fills[0].filled_at, next_open)
        self.assertEqual(fills[0].event_id, "event-1")
        self.assertAlmostEqual(holdings["A"].average_entry_price, 10.1)
        self.assertAlmostEqual(cash, 100 - 2 * 10.1 * 1.01)

    def test_invalid_target_schema_rejected(self):
        with self.assertRaisesRegex(StrategyValidationError, "숫자"):
            evaluate_state("s", universe("A"), {"A": None})
        with self.assertRaisesRegex(StrategyValidationError, "유니버스 밖"):
            evaluate_state("s", universe("A"), {"B": 0.5})

    def test_four_histories_separate_signal_from_trade(self):
        result = evaluate_events("s", universe("A"), {"A"}, set(), {}, entry_weight=1)
        open_at = T + timedelta(days=1) - timedelta(hours=6, minutes=30)
        bar = MarketBar("A", T + timedelta(days=1), 10, 11, 10, 11, open_at=open_at)
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

    def test_notional_excludes_fee_and_csv_keeps_cash(self):
        next_open = T + timedelta(days=1) - timedelta(hours=6, minutes=30)
        bought = MarketBar("A", T + timedelta(days=1), 10, 11, 10, 11, open_at=next_open)
        buy_result = evaluate_events("s", universe("A"), {"A"}, set(), {}, entry_weight=1)
        bought_book = run_backtest([buy_result], {T: {"A": 10}}, {T: {"A": bought}},
                                   initial_cash=100, fee_rate=.01, slippage_rate=.01)
        fill = bought_book.trade_log[0]
        buy_amount, sell_amount = trade_notional(fill)
        self.assertAlmostEqual(execution_open(fill), 10)
        self.assertAlmostEqual(buy_amount, fill.quantity * 10.1)
        self.assertEqual(sell_amount, 0)
        self.assertAlmostEqual(fill.fee, fill.quantity * 10.1 * .01)
        self.assertNotAlmostEqual(buy_amount, buy_amount + fill.fee)
        rows = trade_rows(bought_book, initial_cash=100)
        self.assertAlmostEqual(rows[0]["cash_after"], 100 - buy_amount - fill.fee)
        self.assertAlmostEqual(rows[0]["buy_amount"], buy_amount)

        sell_at = next_open + timedelta(days=1)
        sold = MarketBar("A", T + timedelta(days=2), 12, 13, 12, 13, open_at=sell_at)
        sells, _, cash = fill_at_next_open(
            [OrderIntent("A", "SELL", fill.quantity, next_open)],
            {"A": sold}, {"A": PositionState("A", fill.quantity, fill.price)},
            rows[0]["cash_after"], fee_rate=.01, slippage_rate=.01,
        )
        _, sell_notional = trade_notional(sells[0])
        self.assertAlmostEqual(execution_open(sells[0]), 12)
        self.assertAlmostEqual(sell_notional, fill.quantity * 12 * .99)
        self.assertAlmostEqual(cash, rows[0]["cash_after"] + sell_notional - sells[0].fee)
        with TemporaryDirectory() as folder:
            path = write_trades_csv(bought_book, f"{folder}/trades.csv", initial_cash=100)
            text = path.read_text(encoding="utf-8").splitlines()
        self.assertIn("buy_amount", text[0])
        self.assertEqual(len(text), 2)


if __name__ == "__main__":
    unittest.main()
