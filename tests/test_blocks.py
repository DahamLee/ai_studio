import unittest
from datetime import date, timedelta

from ai_studio.n08_blocks import (
    allocate_target_weight,
    apply_stop,
    breakout_condition,
    combine_conditions,
    compose_universe,
    exclude_universe,
    min_price_universe,
    if_else_score,
    low_volatility_score,
    momentum_score,
    moving_average_cross,
    rebalance_weights,
    rsi_indicator,
    set_operation,
    trend_condition,
)
from ai_studio.n01_frames import MarketBar, ScoreFrame, ScoreRow, SelectionFrame, SelectionRow, ConditionFrame, ConditionRow, WeightFrame, WeightRow
from ai_studio.n03_market_data import daily_bar, history_from_bars, session_close as at


def bars(symbol: str, closes: list[float], *, high: list[float] | None = None, volume: float = 1_000) -> list[MarketBar]:
    start = date(2026, 1, 1)
    rows = []
    for index, close in enumerate(closes):
        peak = close if high is None else high[index]
        rows.append(daily_bar(symbol, start + timedelta(days=index), close, peak, close, close, volume))
    return rows


class BlockTests(unittest.TestCase):
    def test_universe_drops_short_history_halted_and_thin_names(self):
        history = history_from_bars(
            bars("LONG", [10, 10, 10, 10], volume=50)
            + bars("SHORT", [10, 10])
            + [daily_bar("HALT", "2026-01-01", 10, 10, 10, 10, 50)]
            + bars("THIN", [10, 10, 10, 10], volume=1)
        )
        universe = compose_universe(history, at("2026-01-04"))
        self.assertEqual(universe.symbols, ("HALT", "LONG", "SHORT", "THIN"))
        kept = exclude_universe(
            history, universe.symbols, at("2026-01-04"),
            min_listing_days=4, min_average_turnover=100, turnover_window=2,
        )
        self.assertEqual(kept.symbols, ("LONG",))
        with self.assertRaisesRegex(ValueError, "순이익"):
            exclude_universe(history, universe.symbols, at("2026-01-04"), exclude_loss_making=True)

    def test_min_price_keeps_closes_at_or_above_the_floor(self):
        history = history_from_bars(
            bars("RICH", [900, 1000]) + bars("POOR", [1000, 999]) + bars("GONE", [2000])
        )
        kept = min_price_universe(history, ("RICH", "POOR", "GONE"), at("2026-01-02"), min_close=1000)
        self.assertEqual(kept.symbols, ("RICH",))
        untouched = min_price_universe(history, ("RICH", "POOR"), at("2026-01-02"), min_close=0)
        self.assertEqual(untouched.symbols, ("RICH", "POOR"))
        with self.assertRaisesRegex(ValueError, "최소 가격"):
            min_price_universe(history, ("RICH",), at("2026-01-02"), min_close=-1)

    def test_momentum_skips_the_latest_month(self):
        closes = [100] * 22 + [80] + [50] * 21
        history = history_from_bars(bars("A", closes))
        score = momentum_score(history, ("A",), at("2026-02-13"), lookback_months=1, skip_months=1)
        # 최근 21거래일의 50은 빠지고, 그 앞 종가 80과 21거래일 전 100을 비교한다.
        self.assertAlmostEqual(score.rows[0].score, -0.2)

        rising = [100] * 22
        rising[21] = 150
        history = history_from_bars(bars("A", rising))
        score = momentum_score(history, ("A",), at("2026-01-22"), lookback_months=1, skip_months=0)
        self.assertAlmostEqual(score.rows[0].score, 0.5)

    def test_trend_breakout_and_cross_use_price_only(self):
        history = history_from_bars(bars("UP", [1, 1, 1, 4]) + bars("DOWN", [4, 4, 4, 1]))
        trend = trend_condition(history, ("UP", "DOWN"), at("2026-01-04"), slow_days=3)
        self.assertEqual(trend.matches(), {"UP": True, "DOWN": False})
        crossed = trend_condition(history, ("UP",), at("2026-01-04"), method="ma_cross", fast_days=2, slow_days=3)
        self.assertTrue(crossed.matches()["UP"])

        breakout = breakout_condition(
            history_from_bars(bars("A", [5, 6, 7], high=[5, 6, 7])),
            ("A",), at("2026-01-03"), window_days=2,
        )
        self.assertTrue(breakout.matches()["A"])
        inside = breakout_condition(
            history_from_bars(bars("A", [5, 9, 8], high=[5, 9, 8])),
            ("A",), at("2026-01-03"), window_days=2,
        )
        self.assertFalse(inside.matches()["A"])

        cross = moving_average_cross(
            history_from_bars(bars("AAPL", [3, 2, 1, 1, 3, 4])),
            ("AAPL",), at("2026-01-06"), fast_days=2, slow_days=3,
        )
        self.assertEqual([event.kind for event in cross.rows], ["entry"])

    def test_low_volatility_prefers_the_calmer_name(self):
        calm = [10, 10, 10, 10, 10]
        wild = [10, 12, 9, 13, 8]
        history = history_from_bars(bars("CALM", calm) + bars("WILD", wild))
        scores = {
            row.instrument_id: row.score
            for row in low_volatility_score(history, ("CALM", "WILD"), at("2026-01-05"), window_days=4).rows
        }
        self.assertGreater(scores["CALM"], scores["WILD"])
        beta = low_volatility_score(
            history_from_bars(bars("A", [10, 11, 12, 13, 14]) + bars("B", [10, 11, 12, 13, 14])),
            ("A", "B"), at("2026-01-05"), metric="beta", window_days=4,
        )
        self.assertTrue(all(abs(row.raw_value - 1) < 1e-9 for row in beta.rows))

    def test_rsi_logic_weight_stop_and_rebalance(self):
        history = history_from_bars(bars("AAPL", [100, 102, 101, 99, 98, 97, 96, 95, 94, 93, 92, 91, 90, 89, 88]))
        indicator = rsi_indicator(history, ("AAPL",), at("2026-01-15"), period=14)
        self.assertAlmostEqual(indicator.rows[0].value, 12.5)

        merged = combine_conditions(
            "and",
            ConditionFrame((ConditionRow("AAPL", True), ConditionRow("MSFT", True))),
            ConditionFrame((ConditionRow("AAPL", False), ConditionRow("MSFT", True))),
        )
        self.assertEqual(merged.matches(), {"AAPL": False, "MSFT": True})
        self.assertEqual(set_operation({"A", "B"}, {"B", "C"}, "intersect"), ("B",))
        chosen = if_else_score(
            ConditionFrame((ConditionRow("AAPL", True), ConditionRow("MSFT", False))),
            ScoreFrame((ScoreRow("AAPL", 1), ScoreRow("MSFT", 1))),
            ScoreFrame((ScoreRow("AAPL", 9), ScoreRow("MSFT", 9))),
        )
        self.assertEqual({row.instrument_id: row.score for row in chosen.rows}, {"AAPL": 1, "MSFT": 9})

        selection = SelectionFrame((SelectionRow("AAPL", 1, 1), SelectionRow("MSFT", 2, 3)))
        weights = allocate_target_weight(selection, "score")
        self.assertAlmostEqual(sum(row.weight for row in weights.rows), 1)
        stopped = apply_stop(
            weights,
            entry_prices={"AAPL": 100, "MSFT": 100},
            last_prices={"AAPL": 80, "MSFT": 140},
            stop_loss_pct=10,
            take_profit_pct=30,
        )
        self.assertEqual({row.instrument_id: row.weight for row in stopped.rows}, {"AAPL": 0, "MSFT": 0})

        target = WeightFrame((WeightRow("AAPL", 0.5), WeightRow("MSFT", 0.5)))
        actual = WeightFrame((WeightRow("AAPL", 0.52), WeightRow("MSFT", 0.48)))
        self.assertEqual(rebalance_weights(target, actual, due=False, drift_threshold_pct=5), actual)
        self.assertEqual(rebalance_weights(target, actual, due=False, drift_threshold_pct=1), target)


if __name__ == "__main__":
    unittest.main()
