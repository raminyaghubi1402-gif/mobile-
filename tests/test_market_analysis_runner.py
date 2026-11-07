import unittest
from unittest.mock import patch

from lili_trader.market_analysis_runner import fetch_and_analyze


class MarketAnalysisRunnerTests(unittest.TestCase):
    @patch("lili_trader.market_analysis_runner.analyze_market")
    @patch("lili_trader.market_analysis_runner.NobitexAdapter")
    def test_fetches_required_and_context_timeframes(self, adapter_cls, analyze):
        adapter = adapter_cls.return_value
        adapter.fetch_history.return_value = [{"close": 1}]
        analyze.return_value.report_fa = "گزارش تست"
        report = fetch_and_analyze("BTCIRT", count=125)

        self.assertEqual(report, "گزارش تست")
        expected = {"1h": "60", "15m": "15", "5m": "5", "4h": "240", "1D": "D"}
        self.assertEqual(adapter.fetch_history.call_count, len(expected))
        actual = {
            call.kwargs["resolution"]: call.kwargs["count"]
            for call in adapter.fetch_history.call_args_list
        }
        self.assertEqual(set(actual), set(expected.values()))
        self.assertTrue(all(count == 125 for count in actual.values()))
        analyze.assert_called_once()
        self.assertEqual(analyze.call_args.args[0], "BTCIRT")
        self.assertEqual(set(analyze.call_args.args[1]), set(expected))


if __name__ == "__main__":
    unittest.main()
