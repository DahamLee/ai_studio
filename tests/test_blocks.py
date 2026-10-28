import unittest
from datetime import date, timedelta

from ai_studio.n08_blocks import (
    allocate_target_weight,
    combine_conditions,
    compose_universe,
    exclude_universe,
    fundamental_indicator,
    fundamental_universe,
    min_price_universe,
    if_else_score,
    rsi_indicator,
)
from ai_studio.n01_frames import MarketBar, ScoreFrame, ScoreRow, SelectionFrame, SelectionRow, ConditionFrame, ConditionRow, WeightFrame, WeightRow
from ai_studio.n03_market_data import Filing, daily_bar, history_from_bars, session_close as at


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

    def test_fundamental_universe_uses_the_latest_filing_published_by_then(self):
        # 2025Q3 실적(ROE 5)은 분기 말이 아니라 업로드일(11-14) 장 마감부터 보인다.
        fundamentals = {
            "AAA": {"ROE": (Filing(at("2025-08-13"), "2025Q2", 25.0), Filing(at("2025-11-14"), "2025Q3", 5.0))},
            "BBB": {"ROE": (Filing(at("2025-08-13"), "2025Q2", 12.0),)},
            "NEW": {"ROE": (Filing(at("2025-11-20"), "2025Q3", 40.0),)},
        }
        symbols = ("AAA", "BBB", "NEW", "NONE")
        before = fundamental_universe(fundamentals, symbols, at("2025-11-13"), account="ROE", operator="gte", threshold=10)
        after = fundamental_universe(fundamentals, symbols, at("2025-11-14"), account="ROE", operator="gte", threshold=10)
        self.assertEqual(before.symbols, ("AAA", "BBB"))  # NEW는 아직 공시 전, NONE은 공시 없음
        self.assertEqual(after.symbols, ("BBB",))
        low = fundamental_universe(fundamentals, symbols, at("2025-11-14"), account="ROE", operator="lt", threshold=10)
        self.assertEqual(low.symbols, ("AAA",))
        with self.assertRaisesRegex(ValueError, "비교"):
            fundamental_universe(fundamentals, symbols, at("2025-11-14"), account="ROE", operator="eq", threshold=10)

    def test_fundamental_indicator_prices_against_published_filings(self):
        history = history_from_bars(bars("AAA", [100, 120]) + bars("LOSS", [50, 50]) + bars("SHORT", [10, 10]))
        quarter = lambda period, day, value: Filing(at(day), period, value)
        fundamentals = {
            "AAA": {
                # 2025Q4 EPS는 1.0으로 처음 올라왔다가 2026-01-02에 2.0으로 정정된다.
                "EPS": (quarter("2025Q1", "2025-05-01", 1.0), quarter("2025Q2", "2025-08-01", 1.0),
                        quarter("2025Q3", "2025-11-01", 1.0), quarter("2025Q4", "2025-12-20", 1.0),
                        quarter("2025Q4", "2026-01-02", 2.0)),
                "BPS": (quarter("2025Q4", "2025-12-20", 40.0),),
                "ROE": (quarter("2025Q4", "2025-12-20", 18.5),),
            },
            "LOSS": {"EPS": tuple(quarter(f"2025Q{q}", f"2025-{q * 3:02d}-28", -1.0) for q in (1, 2, 3, 4))},
            "SHORT": {"EPS": (quarter("2025Q4", "2025-12-20", 1.0),)},
        }
        symbols = ("AAA", "LOSS", "SHORT")
        per = lambda day: {row.instrument_id: row.value for row in fundamental_indicator(history, fundamentals, symbols, at(day), field="per").rows}
        self.assertEqual(per("2026-01-01"), {"AAA": 100 / 4.0})   # 적자(LOSS)·4분기 미만(SHORT)은 값이 없다
        self.assertEqual(per("2026-01-02"), {"AAA": 120 / 5.0})   # 정정 공시와 그날 종가
        pbr = fundamental_indicator(history, fundamentals, symbols, at("2026-01-02"), field="pbr")
        self.assertEqual([(row.instrument_id, row.value) for row in pbr.rows], [("AAA", 3.0)])
        roe = fundamental_indicator(history, fundamentals, symbols, at("2026-01-02"), field="roe")
        self.assertEqual([(row.instrument_id, row.value) for row in roe.rows], [("AAA", 18.5)])

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
