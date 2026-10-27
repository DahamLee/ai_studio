import unittest
from pathlib import Path

from ai_studio.n12_strategies import EXAMPLES, graph_dict
from ai_studio.n17_editor_api import backtest

DATA = Path(__file__).resolve().parents[1] / "data" / "sp500_ohlc_1y.csv"


class BacktestApiTests(unittest.TestCase):
    def test_incomplete_graph_is_rejected_before_running(self) -> None:
        graph = graph_dict(EXAMPLES["golden-cross"][1]())
        graph["edges"] = [edge for edge in graph["edges"] if edge["to"] != "orders"]
        report = backtest({"graph": graph, "settings": {}})
        self.assertFalse(report["ok"])
        self.assertIn("주문 실행", str(report["reason"]))

    @unittest.skipUnless(DATA.exists(), "저장된 일봉이 없다")
    def test_summary_matches_ledger(self) -> None:
        report = backtest({
            "graph": graph_dict(EXAMPLES["golden-cross"][1]()),
            "settings": {"startDate": "", "endDate": "", "signalFrequency": "daily", "initialCapital": 100_000},
        })
        self.assertTrue(report["ok"], report.get("reason"))
        summary = report["summary"]
        self.assertAlmostEqual(summary["finalValue"], report["equity"][-1]["value"])
        self.assertAlmostEqual(summary["cumulativeReturn"], summary["finalValue"] / 100_000 - 1)
        self.assertLessEqual(summary["maxDrawdown"], 0)

    @unittest.skipUnless(DATA.exists(), "저장된 일봉이 없다")
    def test_empty_period_is_reported(self) -> None:
        report = backtest({
            "graph": graph_dict(EXAMPLES["golden-cross"][1]()),
            "settings": {"startDate": "2000-01-01", "endDate": "2000-12-31"},
        })
        self.assertEqual(report, {"ok": False, "reason": "선택한 기간에 거래일이 없습니다."})
