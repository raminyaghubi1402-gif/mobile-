import unittest

from lili_trader.nobitex_data import NobitexCandle
from lili_trader.volume_trading_engine import VolumeTradingEngine


class VolumeTradingEngineTests(unittest.TestCase):
    def setUp(self):
        self.engine = VolumeTradingEngine()
        self.start = 1_800_000_000
        self.history = [
            {
                "timestamp": self.start + i * 300,
                "fetched_at": self.start + i * 300 + 300,
                "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
                "volume": 10.0 + (i % 3),
            }
            for i in range(20)
        ]
        latest_ts = self.start + 20 * 300
        self.snapshot = {
            "spot_only": True,
            "regime": "bullish",
            "volume_history_5m": self.history,
            "latest_5m": {
                "timestamp": latest_ts,
                "fetched_at": latest_ts + 300,
                "open": 100.0, "high": 104.0, "low": 99.0, "close": 103.0,
                "volume": 20.0,
            },
        }

    def test_relative_volume_and_close_breakout_emit_independent_signal(self):
        decision = self.engine.evaluate(self.snapshot)
        self.assertEqual(decision.status, "SIGNAL")
        self.assertEqual(decision.evidence["execution"], "paper_only")
        self.assertEqual(decision.evidence["stop_loss"], 99.0)
        self.assertEqual(decision.evidence["take_profit"], 109.0)
        self.assertGreater(decision.evidence["relative_volume_vs_median"], 1.0)

    def test_volume_not_greater_than_reference_maximum_is_rejected(self):
        self.snapshot["latest_5m"]["volume"] = 12.0
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")

    def test_wick_only_breakout_is_rejected(self):
        self.snapshot["latest_5m"]["close"] = 100.5
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")

    def test_unclosed_stale_or_gapped_reference_candles_are_rejected(self):
        self.snapshot["latest_5m"]["fetched_at"] -= 1
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")
        self.snapshot["latest_5m"]["fetched_at"] += 902
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")
        self.snapshot["latest_5m"]["fetched_at"] = self.snapshot["latest_5m"]["timestamp"] + 300
        self.snapshot["volume_history_5m"][10]["timestamp"] += 300
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")

    def test_non_bullish_and_non_spot_snapshots_are_rejected(self):
        self.snapshot["regime"] = "range"
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")
        self.snapshot["regime"] = "bullish"
        self.snapshot["spot_only"] = False
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")


if __name__ == "__main__":
    unittest.main()
