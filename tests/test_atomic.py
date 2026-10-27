"""원자 노드 단위 테스트와, 프리셋이 손으로 계산한 기준값과 같은지 확인한다."""

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
    eligible_scores, holding_period_exit_events, market_series, stop_exit_events,
)
from ai_studio.n12_strategies import place
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


def closes_until(symbol: str, day) -> list[float]:
    return [bar.close for bar in HISTORY[symbol] if bar.timestamp <= day]


def preset_run(preset_id: str, day, params=None, **context):
    placed = place(preset_id, "p", {"universe": ("u", "universe")}, params)
    return run(placed.nodes, placed.edges, day, **context), placed


class PresetTests(unittest.TestCase):
    """프리셋(원자 블록 조합)이 손으로 계산한 기준값과 같은지 확인한다."""

    def test_momentum_score(self):
        for day in DAYS[::10]:
            out, placed = preset_run("momentum-score", day, {"ret": {"lookbackDays": 42, "skipDays": 21}})
            got = {r.instrument_id: r.score for r in out.port(*placed.out("score")).rows}
            for symbol in HISTORY:
                closes = closes_until(symbol, day)
                self.assertAlmostEqual(got[symbol], closes[-22] / closes[-64] - 1)

    def test_trend_filter(self):
        for day in DAYS[::7]:
            out, placed = preset_run("trend-filter", day, {"ma": {"windowDays": 30}})
            expected = {s: closes_until(s, day)[-1] > sum(closes_until(s, day)[-30:]) / 30 for s in HISTORY}
            self.assertEqual(out.port(*placed.out("condition")).matches(), expected)

    def test_breakout(self):
        for day in DAYS[::7]:
            out, placed = preset_run("breakout", day, {"high": {"windowDays": 20}, "cmp": {"offsetPct": 1}})
            expected = {}
            for symbol in HISTORY:
                bars = [bar for bar in HISTORY[symbol] if bar.timestamp <= day]
                expected[symbol] = bars[-1].close >= max(bar.high for bar in bars[-21:-1]) * 1.01
            self.assertEqual(out.port(*placed.out("condition")).matches(), expected)

    def test_low_volatility_score(self):
        out, placed = preset_run("low-volatility-score", DAYS[-1], {"vol": {"windowDays": 40}})
        got = {r.instrument_id: r.score for r in out.port(*placed.out("score")).rows}
        for symbol in HISTORY:
            closes = closes_until(symbol, DAYS[-1])[-41:]
            returns = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
            mean = sum(returns) / len(returns)
            deviation = (sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)) ** 0.5
            self.assertAlmostEqual(got[symbol], -deviation)

    def test_golden_cross(self):
        def above(symbol, day):
            closes = closes_until(symbol, day)
            return sum(closes[-5:]) / 5 > sum(closes[-20:]) / 20

        found = 0
        for previous, day in zip(DAYS, DAYS[1:]):
            out, placed = preset_run("golden-cross", day, {"fast": {"windowDays": 5}, "slow": {"windowDays": 20}})
            entries = {r.instrument_id for r in out.port(*placed.out("entry")).rows}
            exits = {r.instrument_id for r in out.port(*placed.out("exit")).rows}
            self.assertEqual(entries, {s for s in HISTORY if not above(s, previous) and above(s, day)}, day)
            self.assertEqual(exits, {s for s in HISTORY if above(s, previous) and not above(s, day)}, day)
            found += len(entries) + len(exits)
        self.assertGreater(found, 0)

    def test_market_regime_is_one_row(self):
        out, placed = preset_run("market-regime", DAYS[-1], {"ma": {"windowDays": 20}})
        self.assertEqual(list(out.port(*placed.out("condition")).matches()), ["MARKET"])

    def test_value_score_needs_fundamentals(self):
        with self.assertRaisesRegex(ValueError, "재무"):
            preset_run("value-score", DAYS[-1])


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
        scores = ScoreFrame((ScoreRow("A", 1), ScoreRow("B", 2), ScoreRow("C", 3)))
        self.assertEqual([r.instrument_id for r in eligible_scores(scores, condition).rows], ["A", "B"])
        market_off = ConditionFrame((ConditionRow("MARKET", False),))
        self.assertEqual(eligible_scores(scores, market_off).rows, ())
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
