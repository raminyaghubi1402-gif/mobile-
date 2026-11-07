import unittest
from unittest.mock import patch

from lili_trader.market_analysis_runner import fetch_and_analyze, _closed_only, _is_fresh_closed_history, _has_interval_gaps


class MarketAnalysisRunnerTests(unittest.TestCase):
    @patch("lili_trader.market_analysis_runner.analyze_market")
    @patch("lili_trader.market_analysis_runner.NobitexAdapter")
    def test_fetches_required_and_context_timeframes(self, adapter_cls, analyze):
        adapter = adapter_cls.return_value
        adapter.fetch_history.side_effect = lambda **kwargs: [{
            "timestamp": 1000, "fetched_at": 1000 + {"60": 3600, "15": 900, "5": 300, "240": 14400, "D": 86400}[kwargs["resolution"]],
            "close": 1,
        }]
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
        self.assertTrue(all(requested == 125 for requested in actual.values()))
        analyze.assert_called_once()
        self.assertEqual(analyze.call_args.args[0], "BTCIRT")
        self.assertEqual(set(analyze.call_args.args[1]), set(expected))

    def test_excludes_currently_forming_candle_for_each_timeframe(self):
        durations = {"1h": 3600, "15m": 900, "5m": 300, "4h": 14400, "1D": 86400}
        for timeframe, duration in durations.items():
            closed = {"timestamp": 1000, "fetched_at": 1000 + duration,
                      "open": 10, "high": 11, "low": 9, "close": 10}
            forming = {"timestamp": 2000, "fetched_at": 2000 + duration - 1,
                       "open": 10, "high": 11, "low": 9, "close": 10}
            result = _closed_only([closed, forming], timeframe)
            self.assertEqual(result, [closed], timeframe)

    def test_stale_closed_candles_are_rejected(self):
        # 15m candle ended 4 intervals ago; too old for a fresh analysis.
        stale = {"timestamp": 1000, "fetched_at": 1000 + 900 + 4 * 900}
        fresh = {"timestamp": 1000, "fetched_at": 1000 + 900 + 2 * 900}
        self.assertFalse(_is_fresh_closed_history([stale], "15m"))
        self.assertTrue(_is_fresh_closed_history([fresh], "15m"))

    def test_interval_gap_detection_rejects_missing_duplicate_and_malformed_bars(self):
        duration = 900
        complete = [{"timestamp": 1000 + i * duration} for i in range(4)]
        missing = [complete[0], complete[1], complete[3]]
        duplicate = [complete[0], complete[1], dict(complete[1])]
        malformed = [complete[0], {"timestamp": 1900.5}]
        self.assertFalse(_has_interval_gaps(complete, "15m"))
        self.assertTrue(_has_interval_gaps(missing, "15m"))
        self.assertTrue(_has_interval_gaps(duplicate, "15m"))
        self.assertTrue(_has_interval_gaps(malformed, "15m"))
        self.assertTrue(_has_interval_gaps([{"timestamp": True}], "15m"))
        self.assertTrue(_has_interval_gaps([{}], "15m"))

    def test_missing_freshness_metadata_is_not_trusted(self):
        candle = {"timestamp": 1000}
        self.assertFalse(_is_fresh_closed_history([candle], "1h"))

    def test_keeps_custom_candles_without_fetch_metadata(self):
        candle = {"open": 10, "high": 11, "low": 9, "close": 10}
        self.assertEqual(_closed_only([candle], "1h"), [candle])

    def test_drops_candle_with_invalid_fetch_timestamps(self):
        invalid = {"timestamp": "bad", "fetched_at": "bad"}
        self.assertEqual(_closed_only([invalid], "15m"), [])


if __name__ == "__main__":
    unittest.main()
