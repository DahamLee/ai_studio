import unittest
from pathlib import Path

from ai_studio.n03_market_data import DATA_PATH
from ai_studio.n15_ohlc_backtest import run_momentum_topn

CSV = Path(DATA_PATH)


class OhlcBacktestTests(unittest.TestCase):
    @unittest.skipUnless(CSV.exists(), "일봉 CSV가 없습니다.")
    def test_momentum_topn_fills_after_signal_and_keeps_cash(self):
        result = run_momentum_topn()
        self.assertGreater(len(result.trade_log), 0)
        self.assertTrue(all(fill.filled_at > fill.signal_at for fill in result.trade_log))
        self.assertTrue(all(row.cash >= -1e-6 for row in result.portfolio_history))
        self.assertGreater(result.portfolio_history[-1].total_value, 0)
