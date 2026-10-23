"""원자 노드 단위 테스트와, 원자 노드 조합이 기존 복합 노드와 같은 결과를 내는지 확인한다."""

import unittest
from datetime import date, timedelta

from ai_studio.n01_frames import (
    ConditionFrame, ConditionRow, IndicatorFrame, IndicatorRow, ScoreFrame, ScoreRow, SelectionFrame,
    SelectionRow, UniverseFrame, WeightFrame, WeightRow,
)
from ai_studio.n02_schema import StrategyEdge, StrategyGraph, StrategyNode
from ai_studio.n03_market_data import daily_bar, history_from_bars, session_close as at
from ai_studio.n05_signals import compare_indicators, negate
from ai_studio.n06_portfolio import (
    combine_scores, scale_when_false, score_from_indicator, select_matched, select_top_n_with_buffer,
    weight_by_indicator,
)
from ai_studio.n07_runtime import PositionState
from ai_studio.n08_blocks import (
    filter_universe, holding_period_exit_events, market_series, stop_exit_events,
)
from ai_studio.n14_engine import ExecutionContext, execute

START = date(2026, 1, 1)


def walk(symbol: str, count: int, seed: int) -> list:
    """결정적인 의사난수 가격 경로."""
    value, state, rows = 100.0, seed, []
    for index in range(count):
        state = (state * 1103515245 + 12345) % 2**31
        value *= 1 + ((state % 2001) - 1000) / 25_000
        high = value * 1.01
        rows.append(daily_bar(symbol, START + timedelta(days=index), value, high, value * 0.99, value, 1_000))
    return rows


HISTORY = history_from_bars(walk("A", 160, 1) + walk("B", 160, 2) + walk("C", 160, 3) + walk("D", 160, 4))
DAYS = [at(START + timedelta(days=index)) for index in range(70, 160)]


def run(nodes, edges, as_of, **context):
    graph = StrategyGraph((StrategyNode("u", "universe.compose", {}),) + tuple(nodes), tuple(edges))
    return execute(graph, ExecutionContext(as_of, HISTORY, **context))


def u(to: str, port: str = "universe") -> StrategyEdge:
    return StrategyEdge("u", "universe", to, port)


class EquivalenceTests(unittest.TestCase):
    """step 3에서 복합 노드를 프리셋으로 바꿔도 결과가 같아야 한다."""

    def test_return_score_equals_momentum_factor(self):
        for day in DAYS[::10]:
            out = run((
                StrategyNode("old", "momentum.factor", {"lookbackMonths": 2, "skipMonths": 1}),
                StrategyNode("ret", "indicator.return", {"lookbackDays": 42, "skipDays": 21}),
                StrategyNode("score", "score.from-indicator", {}),
            ), (u("old"), u("ret"), StrategyEdge("ret", "value", "score", "value")), day)
            old = {r.instrument_id: r.score for r in out.port("old", "score").rows}
            new = {r.instrument_id: r.score for r in out.port("score", "score").rows}
            self.assertEqual(old.keys(), new.keys())
            for key in old:
                self.assertAlmostEqual(old[key], new[key])

    def test_price_vs_average_equals_trend(self):
        for day in DAYS[::7]:
            out = run((
                StrategyNode("old", "momentum.trend", {"method": "price_above_ma", "slowDays": 30}),
                StrategyNode("price", "indicator.price", {}),
                StrategyNode("ma", "indicator.moving-average", {"windowDays": 30}),
                StrategyNode("cmp", "condition.compare", {"operator": "gt"}),
            ), (u("old"), u("price"), u("ma"),
                StrategyEdge("price", "value", "cmp", "left"), StrategyEdge("ma", "value", "cmp", "right")), day)
            self.assertEqual(out.port("old", "condition").matches(), out.port("cmp", "condition").matches())

    def test_close_vs_prior_high_equals_breakout(self):
        for day in DAYS[::7]:
            out = run((
                StrategyNode("old", "momentum.breakout", {"windowDays": 20, "bufferPct": 1}),
                StrategyNode("price", "indicator.price", {}),
                StrategyNode("high", "indicator.high", {"windowDays": 20}),
                StrategyNode("cmp", "condition.compare", {"operator": "gte", "offsetPct": 1}),
            ), (u("old"), u("price"), u("high"),
                StrategyEdge("price", "value", "cmp", "left"), StrategyEdge("high", "value", "cmp", "right")), day)
            self.assertEqual(out.port("old", "condition").matches(), out.port("cmp", "condition").matches())

    def test_volatility_score_equals_low_volatility(self):
        out = run((
            StrategyNode("old", "factor.low-volatility", {"windowDays": 40}),
            StrategyNode("vol", "indicator.volatility", {"windowDays": 40}),
            StrategyNode("score", "score.from-indicator", {"direction": "lower"}),
        ), (u("old"), u("vol"), StrategyEdge("vol", "value", "score", "value")), DAYS[-1])
        old = {r.instrument_id: r.score for r in out.port("old", "score").rows}
        new = {r.instrument_id: r.score for r in out.port("score", "score").rows}
        self.assertEqual(old.keys(), new.keys())
        for key in old:
            self.assertAlmostEqual(old[key], new[key])

    def test_average_cross_transition_equals_ma_cross(self):
        found = 0
        for day in DAYS:
            out = run((
                StrategyNode("old", "momentum.ma-cross", {"fastDays": 5, "slowDays": 20}),
                StrategyNode("fast", "indicator.moving-average", {"windowDays": 5}),
                StrategyNode("slow", "indicator.moving-average", {"windowDays": 20}),
                StrategyNode("cmp", "condition.compare", {"operator": "gt"}),
                StrategyNode("edge", "event.transition", {}),
            ), (u("old"), u("fast"), u("slow"),
                StrategyEdge("fast", "value", "cmp", "left"), StrategyEdge("slow", "value", "cmp", "right"),
                StrategyEdge("cmp", "condition", "edge", "condition")), day)
            for kind in ("entry", "exit"):
                old = {r.instrument_id for r in out.port("old", kind).rows if r.as_of == day}
                new = {r.instrument_id for r in out.port("edge", kind).rows}
                self.assertEqual(old, new, f"{day} {kind}")
                found += len(new)
        self.assertGreater(found, 0)


class AtomicTests(unittest.TestCase):
    def test_score_methods_and_direction(self):
        values = IndicatorFrame((IndicatorRow("A", 1), IndicatorRow("B", 3), IndicatorRow("C", 2)))
        rank = {r.instrument_id: r.score for r in score_from_indicator(values, "lower", "rank").rows}
        self.assertEqual(rank, {"A": 1.0, "B": 0.0, "C": 0.5})
        z = score_from_indicator(values, "higher", "zscore")
        self.assertAlmostEqual(sum(r.score for r in z.rows), 0)
        self.assertEqual([r.raw_value for r in z.rows], [1, 3, 2])

    def test_combine_scores_keeps_names_in_both(self):
        left = ScoreFrame((ScoreRow("A", 1), ScoreRow("B", 0)))
        right = ScoreFrame((ScoreRow("A", 0), ScoreRow("C", 1)))
        self.assertEqual([(r.instrument_id, r.score) for r in combine_scores(left, right, 0.25).rows], [("A", 0.25)])

    def test_market_row_broadcasts_and_not_flips(self):
        stocks = IndicatorFrame((IndicatorRow("A", 5), IndicatorRow("B", 1)))
        market = IndicatorFrame((IndicatorRow("MARKET", 2),))
        self.assertEqual(compare_indicators(stocks, market, "gt").matches(), {"A": True, "B": False})
        self.assertEqual(negate(compare_indicators(market, stocks, "gt")).matches(), {"A": True, "B": False})

    def test_hold_buffer_keeps_slightly_lower_holding(self):
        scores = ScoreFrame(tuple(ScoreRow(s, v) for s, v in (("A", 5), ("B", 4), ("C", 3), ("D", 2))))
        plain = select_top_n_with_buffer(scores, 2, set(), 0)
        self.assertEqual([r.instrument_id for r in plain.rows], ["A", "B"])
        kept = select_top_n_with_buffer(scores, 2, {"C"}, 1)
        self.assertEqual([r.instrument_id for r in kept.rows], ["A", "C"])
        dropped = select_top_n_with_buffer(scores, 2, {"D"}, 1)
        self.assertEqual([r.instrument_id for r in dropped.rows], ["A", "B"])

    def test_condition_selection_filter_and_inverse_weight(self):
        condition = ConditionFrame((ConditionRow("B", True), ConditionRow("A", True), ConditionRow("C", False)))
        self.assertEqual([r.instrument_id for r in select_matched(condition).rows], ["A", "B"])
        universe = UniverseFrame(at("2026-01-02"), ("A", "B", "C"))
        self.assertEqual(filter_universe(universe, condition).symbols, ("A", "B"))
        weights = weight_by_indicator(SelectionFrame((SelectionRow("A", 1), SelectionRow("B", 2))),
                                      IndicatorFrame((IndicatorRow("A", 0.1), IndicatorRow("B", 0.3))))
        self.assertAlmostEqual(dict((r.instrument_id, r.weight) for r in weights.rows)["A"], 0.75)

    def test_risk_off_moves_weight_to_cash(self):
        weights = WeightFrame((WeightRow("A", 0.5), WeightRow("B", 0.5)))
        off = scale_when_false(weights, ConditionFrame((ConditionRow("MARKET", False),)), 0.2)
        self.assertEqual([r.weight for r in off.rows], [0.1, 0.1])
        on = scale_when_false(weights, ConditionFrame((ConditionRow("MARKET", True),)), 0)
        self.assertEqual(on, weights)

    def test_market_series_is_point_in_time(self):
        series = market_series(HISTORY)
        cut = history_from_bars(b for rows in HISTORY.values() for b in rows if b.timestamp <= DAYS[0])
        self.assertAlmostEqual(market_series(cut)[-1].close,
                               next(b.close for b in series if b.timestamp == DAYS[0]))

    def test_stop_and_holding_period_exit_held_names(self):
        day = DAYS[-1]
        last = {s: rows[-1].close for s, rows in HISTORY.items()}
        positions = {
            "A": PositionState("A", 1, last["A"] * 1.2, opened_at=at(START + timedelta(days=150))),
            "B": PositionState("B", 1, last["B"] * 0.95, opened_at=at(START + timedelta(days=150))),
        }
        stopped = stop_exit_events(HISTORY, positions, day, stop_loss_pct=10, take_profit_pct=0)
        self.assertEqual([r.instrument_id for r in stopped.rows], ["A"])
        profit = stop_exit_events(HISTORY, positions, day, stop_loss_pct=0, take_profit_pct=5)
        self.assertEqual([r.instrument_id for r in profit.rows], ["B"])
        aged = holding_period_exit_events(HISTORY, positions, day, max_days=10)
        self.assertEqual([r.instrument_id for r in aged.rows], ["A", "B"])
        self.assertEqual(holding_period_exit_events(HISTORY, positions, day, max_days=11).rows, ())

    def test_empty_indicator_warns_with_required_history(self):
        out = run((StrategyNode("ret", "indicator.return", {"lookbackDays": 500}),), (u("ret"),), DAYS[-1])
        self.assertEqual(out.warnings, ("ret: 결과가 비었습니다. 종목마다 최소 501개 봉이 필요합니다.",))

    def test_market_scope_indicator_is_one_row(self):
        out = run((StrategyNode("ma", "indicator.moving-average", {"windowDays": 20, "scope": "market"}),),
                  (u("ma"),), DAYS[-1])
        self.assertEqual([r.instrument_id for r in out.port("ma", "value").rows], ["MARKET"])


if __name__ == "__main__":
    unittest.main()
