"""목표 구조의 완료 기준: 예제 전략이 노드만으로 원장까지 실행된다 (docs/ARCHITECTURE.md 6.5)."""

import unittest
from datetime import date, timedelta
from pathlib import Path

from ai_studio.n03_market_data import DATA_PATH, daily_bar, history_from_bars, load_ohlc
from ai_studio.n10_functions import get_function
from ai_studio.n12_strategies import EXAMPLES, example_market_regime, example_rsi_reversal
from ai_studio.n13_connect import validate_strategy
from ai_studio.n15_ohlc_backtest import RunSettings, run_graph_backtest

SETTINGS = RunSettings(signal_frequency="daily", initial_capital=100_000, fee_rate=0.001, slippage_rate=0.0005)


def path(symbol: str, start: float, moves: list[float]):
    """일간 수익률 목록으로 일봉을 만든다."""
    value, rows = start, []
    for index, move in enumerate(moves):
        previous, value = value, value * (1 + move)
        rows.append(daily_bar(symbol, date(2026, 1, 1) + timedelta(days=index), previous, max(previous, value),
                              min(previous, value), value, 10_000))
    return rows


class CoverageTests(unittest.TestCase):
    def test_every_example_is_a_complete_graph(self):
        for key, (_, build) in EXAMPLES.items():
            check = validate_strategy(build())
            self.assertTrue(check.ok, f"{key}: {check.reason}")
            self.assertEqual(check.warnings, (), key)

    @unittest.skipUnless(Path(DATA_PATH).exists(), "일봉 CSV가 없습니다.")
    def test_every_example_reaches_the_ledger_on_saved_data(self):
        history = history_from_bars(load_ohlc())
        days = len({bar.timestamp for rows in history.values() for bar in rows})
        turnover = {}
        for key, (_, build) in EXAMPLES.items():
            with self.subTest(key):
                graph = build()
                result = run_graph_backtest(graph, history, SETTINGS)
                self.assertGreater(len(result.trade_log), 0)
                self.assertTrue(all(fill.filled_at > fill.signal_at for fill in result.trade_log))
                self.assertTrue(all(row.cash >= -1e-6 for row in result.portfolio_history))
                self.assertEqual(len(result.portfolio_history), days)
                decision = next(node for node in graph.nodes if get_function(node.function_id).role == "STRATEGY")
                if decision.function_id == "strategy.event":
                    limit = int(decision.params.get("maxPositions", 10))
                    by_day: dict = {}
                    for row in result.position_history:
                        by_day[row.as_of] = by_day.get(row.as_of, 0) + 1
                    self.assertLessEqual(max(by_day.values(), default=0), limit)
                turnover[key] = len(result.trade_log)
        self.assertLess(turnover["hold-buffer"], turnover["momentum"])

    def test_market_below_average_moves_to_cash(self):
        # 120일 오른 뒤 60일 동안 시장 전체가 내린다. 100일선 아래로 내려간 뒤 월말에 모두 판다.
        rise, fall = [0.004] * 120, [-0.012] * 60
        bars = [bar for index, symbol in enumerate("ABCDEFGHIJKL")
                for bar in path(symbol, 50 + index, [m * (1 + index / 20) for m in rise + fall])]
        result = run_graph_backtest(example_market_regime(), history_from_bars(bars), SETTINGS)
        invested = [row for row in result.portfolio_history if row.securities_value > 0]
        self.assertTrue(invested)
        self.assertEqual(result.portfolio_history[-1].securities_value, 0)
        # 4월 말에 사고, 시장이 100일선 아래로 내려간 5월 말에 모두 판 뒤로는 다시 사지 않는다.
        sides = sorted({(fill.signal_at.date(), fill.side) for fill in result.trade_log})
        self.assertEqual(sides, [(date(2026, 4, 30), "BUY"), (date(2026, 5, 31), "SELL")])

    def test_rsi_entry_is_stopped_out_before_the_holding_period(self):
        # 오르다가 하루 1.5%씩 계속 내리면 RSI가 30 아래로 들어가 진입하고, 10% 더 빠지면 손절한다.
        moves = [0.01 if index % 3 else -0.005 for index in range(80)] + [-0.015] * 30
        bars = path("A", 100, moves) + path("B", 100, [0.002] * 110)
        result = run_graph_backtest(example_rsi_reversal(), history_from_bars(bars), SETTINGS)
        buys = [fill for fill in result.trade_log if fill.side == "BUY" and fill.symbol == "A"]
        sells = [fill for fill in result.trade_log if fill.side == "SELL" and fill.symbol == "A"]
        self.assertEqual(len(buys), 1)
        self.assertEqual(len(sells), 1)
        self.assertLess(sells[0].price, buys[0].price * 0.92)
        self.assertLess((sells[0].filled_at - buys[0].filled_at).days, 20)


if __name__ == "__main__":
    unittest.main()
