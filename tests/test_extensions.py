"""확장 블록: 지수이동평균·MACD·볼린저·ATR, 재무 성장률·시가총액, 리밸런스 날짜·무거래 구간."""

import unittest
from datetime import date, datetime, timedelta, timezone

from ai_studio.n01_frames import UniverseFrame
from ai_studio.n03_market_data import Filing, daily_bar, history_from_bars, session_close as at
from ai_studio.n04_indicators import ema_series, macd, true_ranges
from ai_studio.n07_runtime import PositionState, evaluate_state, generate_orders
from ai_studio.n08_blocks import (
    atr_indicator, bollinger_indicator, fundamental_indicator, market_cap_universe, moving_average_indicator,
)
from ai_studio.n15_ohlc_backtest import period_ends, period_starts


def series(symbol: str, closes: list[float], spread: float = 0.0):
    start = date(2026, 1, 1)
    return [daily_bar(symbol, start + timedelta(days=i), c, c + spread, c - spread, c) for i, c in enumerate(closes)]


class IndicatorTests(unittest.TestCase):
    def test_ema_seeds_with_simple_average(self):
        # 기간 3, α = 0.5. 첫 값은 (1+2+3)/3 = 2, 이후 2 + 0.5×(4-2) = 3, 3 + 0.5×(8-3) = 5.5
        self.assertEqual(ema_series([1, 2, 3, 4, 8], 3), [2, 3, 5.5])

    def test_macd_of_flat_prices_is_zero_and_rising_prices_positive(self):
        self.assertEqual(macd([10.0] * 40, 3, 6, 3), (0.0, 0.0, 0.0))
        line, signal, histogram = macd([float(i) for i in range(1, 41)], 3, 6, 3)
        self.assertGreater(line, 0)
        self.assertAlmostEqual(histogram, line - signal)
        with self.assertRaisesRegex(ValueError, "부족"):
            macd([1.0] * 7, 3, 6, 3)

    def test_ema_option_reacts_faster_than_sma(self):
        history = history_from_bars(series("A", [10.0] * 10 + [20.0]))
        sma = moving_average_indicator(history, ("A",), at("2026-01-11"), window_days=5).rows[0].value
        ema = moving_average_indicator(history, ("A",), at("2026-01-11"), window_days=5, method="ema").rows[0].value
        self.assertEqual(sma, 12.0)
        self.assertGreater(ema, sma)

    def test_bollinger_lines(self):
        history = history_from_bars(series("A", [1.0, 3.0, 1.0, 3.0]))
        value = lambda line: bollinger_indicator(history, ("A",), at("2026-01-04"), window_days=4, width=2, line=line).rows[0].value
        # 평균 2, 모집단 표준편차 1 → 상단 4, 하단 0, 종가 3은 %B 0.75, 밴드폭 4/2 = 200%
        self.assertEqual((value("upper"), value("lower"), value("percent_b"), value("bandwidth")), (4.0, 0.0, 0.75, 200.0))

    def test_atr_counts_gaps_from_previous_close(self):
        self.assertEqual(true_ranges([11, 15], [9, 13], [10, 14]), [5])  # |15 - 10| = 5가 고저폭 2보다 크다
        history = history_from_bars(series("A", [10.0, 14.0, 14.0], spread=1.0))
        atr = atr_indicator(history, ("A",), at("2026-01-03"), period_days=2).rows[0].value
        self.assertEqual(atr, (5 + 2) / 2)
        pct = atr_indicator(history, ("A",), at("2026-01-03"), period_days=2, unit="percent").rows[0].value
        self.assertAlmostEqual(pct, 3.5 / 14 * 100)


class FundamentalExtensionTests(unittest.TestCase):
    FUNDAMENTALS = {
        "A": {
            "REVENUE": (Filing(at("2025-02-10"), "2024Q4", 100.0), Filing(at("2026-02-10"), "2025Q4", 120.0)),
            "NET_INCOME": (Filing(at("2025-02-10"), "2024Q4", -10.0), Filing(at("2026-02-10"), "2025Q4", 5.0)),
            "SHARES_OUTSTANDING": (Filing(at("2025-11-10"), "2025Q3", 1000.0),),
        },
        "B": {"SHARES_OUTSTANDING": (Filing(at("2025-11-10"), "2025Q3", 100.0),)},
    }

    def test_yoy_growth_uses_same_quarter_last_year_once_published(self):
        history = history_from_bars(series("A", [50.0] * 3))
        grow = lambda field, day: {r.instrument_id: r.value for r in fundamental_indicator(
            history, self.FUNDAMENTALS, ("A", "B"), at(day), field=field).rows}
        self.assertEqual(grow("revenue_yoy", "2026-02-09"), {})            # 2025Q4는 아직 공시 전
        self.assertEqual(grow("revenue_yoy", "2026-02-10"), {"A": 20.0})
        self.assertEqual(grow("net_income_yoy", "2026-02-10"), {"A": 150.0})  # 적자 → 흑자는 |전년|으로 나눠 +

    def test_market_cap_floor_and_top_n(self):
        history = history_from_bars(series("A", [50.0]) + series("B", [100.0]) + series("C", [10.0]))
        day = at("2026-01-01")
        # A: 50 × 1000백만 주 = 50십억, B: 100 × 100 = 10십억, C: 발행주식수 없음
        self.assertEqual(market_cap_universe(history, self.FUNDAMENTALS, ("A", "B", "C"), day, min_cap=20).symbols, ("A",))
        self.assertEqual(market_cap_universe(history, self.FUNDAMENTALS, ("A", "B", "C"), day, top_n=1).symbols, ("A",))
        self.assertEqual(market_cap_universe(history, self.FUNDAMENTALS, ("A", "B", "C"), day).symbols, ("A", "B"))


class ExecutionOptionTests(unittest.TestCase):
    def test_rebalance_day_first_or_last_of_period(self):
        days = [at(d) for d in ("2026-01-02", "2026-01-30", "2026-02-02", "2026-02-27")]
        self.assertEqual(period_ends(days, "monthly"), (days[1], days[3]))
        self.assertEqual(period_starts(days, "monthly"), (days[0], days[2]))

    def test_no_trade_band_skips_small_adjustments_only(self):
        as_of = datetime(2026, 1, 2, 21, tzinfo=timezone.utc)
        result = evaluate_state("s", UniverseFrame(as_of, ("A", "B", "C")), {"A": 0.5, "B": 0.5})
        held = {"A": PositionState("A", 48, 10), "C": PositionState("C", 10, 10)}
        prices = {"A": 10.0, "B": 10.0, "C": 10.0}
        # A는 48% → 50%(2%p 차이), B는 신규, C는 전량 청산
        banded = generate_orders(result, held, prices, portfolio_value=1000, cash=420, no_trade_band=0.03)
        self.assertEqual(sorted((o.symbol, o.side) for o in banded), [("B", "BUY"), ("C", "SELL")])
        plain = generate_orders(result, held, prices, portfolio_value=1000, cash=420)
        self.assertIn(("A", "BUY"), [(o.symbol, o.side) for o in plain])


if __name__ == "__main__":
    unittest.main()
