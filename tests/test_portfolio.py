import unittest

from ai_studio.frames import IndicatorFrame, IndicatorRow, ScoreFrame, ScoreRow, TargetPortfolio
from ai_studio.portfolio import (
    apply_cash_weight,
    cap_max_weight,
    compare_threshold,
    select_min_score,
    select_top_n,
    to_target_portfolio,
    weight_by_score,
    weight_equal,
)


def scores(*pairs: tuple[str, float]) -> ScoreFrame:
    return ScoreFrame(tuple(ScoreRow(symbol, value) for symbol, value in pairs))


class PortfolioStageTests(unittest.TestCase):
    def test_score_selection_weight_constraint_are_separate(self):
        ranked = select_top_n(scores(("AAPL", 0.2), ("MSFT", 0.9), ("NVDA", 0.4)), 2)
        self.assertEqual([row.instrument_id for row in ranked.rows], ["MSFT", "NVDA"])
        self.assertEqual([row.rank for row in ranked.rows], [1, 2])

        weights = weight_equal(ranked)
        self.assertAlmostEqual(sum(row.weight for row in weights.rows), 1)
        capped = cap_max_weight(weights, 0.4)
        self.assertTrue(all(row.weight <= 0.4 for row in capped.rows))
        with_cash = apply_cash_weight(weights, 0.2)
        target = to_target_portfolio(with_cash)
        self.assertAlmostEqual(target.cash_weight, 0.2)
        self.assertNotIn("quantity", TargetPortfolio.__dataclass_fields__)

    def test_higher_score_is_preferred_and_ties_use_id(self):
        ranked = select_top_n(scores(("MSFT", 1), ("AAPL", 1), ("NVDA", 0)), 2)
        self.assertEqual([row.instrument_id for row in ranked.rows], ["AAPL", "MSFT"])

    def test_min_score_does_not_assign_weights(self):
        selected = select_min_score(scores(("AAPL", 0.2), ("MSFT", 0.8)), 0.5)
        self.assertEqual([row.instrument_id for row in selected.rows], ["MSFT"])

    def test_score_weight_uses_selection_scores_only(self):
        selected = select_top_n(scores(("AAPL", 1), ("MSFT", 3)), 2)
        weights = weight_by_score(selected)
        by_id = {row.instrument_id: row.weight for row in weights.rows}
        self.assertAlmostEqual(by_id["MSFT"], 0.75)
        self.assertAlmostEqual(by_id["AAPL"], 0.25)

    def test_rsi_threshold_is_a_condition_not_a_score(self):
        result = compare_threshold(
            IndicatorFrame((IndicatorRow("AAPL", 28), IndicatorRow("MSFT", 55))),
            "lt",
            30,
        )
        self.assertEqual(result, {"AAPL": True, "MSFT": False})

    def test_target_portfolio_has_no_order_quantity(self):
        target = to_target_portfolio(weight_equal(select_top_n(scores(("AAPL", 1),), 1)))
        self.assertEqual(target.positions[0].target_weight, 1)
        self.assertEqual(target.cash_weight, 0)
        self.assertFalse(hasattr(target.positions[0], "quantity"))
