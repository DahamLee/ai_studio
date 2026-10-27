import unittest
from datetime import date, timedelta

from ai_studio.n08_blocks import (
    allocate_target_weight,
    combine_conditions,
    compose_universe,
    exclude_universe,
    min_price_universe,
    if_else_score,
    rsi_indicator,
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

    def test_rsi_logic_and_weight(self):
        history = history_from_bars(bars("AAPL", [100, 102, 101, 99, 98, 97, 96, 95, 94, 93, 92, 91, 90, 89, 88]))
        indicator = rsi_indicator(history, ("AAPL",), at("2026-01-15"), period=14)
        self.assertAlmostEqual(indicator.rows[0].value, 12.5)

        merged = combine_conditions(
            "and",
            ConditionFrame((ConditionRow("AAPL", True), ConditionRow("MSFT", True))),
            ConditionFrame((ConditionRow("AAPL", False), ConditionRow("MSFT", True))),
        )
        self.assertEqual(merged.matches(), {"AAPL": False, "MSFT": True})
        chosen = if_else_score(
            ConditionFrame((ConditionRow("AAPL", True), ConditionRow("MSFT", False))),
            ScoreFrame((ScoreRow("AAPL", 1), ScoreRow("MSFT", 1))),
            ScoreFrame((ScoreRow("AAPL", 9), ScoreRow("MSFT", 9))),
        )
        self.assertEqual({row.instrument_id: row.score for row in chosen.rows}, {"AAPL": 1, "MSFT": 9})

        selection = SelectionFrame((SelectionRow("AAPL", 1, 1), SelectionRow("MSFT", 2, 3)))
        weights = allocate_target_weight(selection, "score")
        self.assertAlmostEqual(sum(row.weight for row in weights.rows), 1)


if __name__ == "__main__":
    unittest.main()
