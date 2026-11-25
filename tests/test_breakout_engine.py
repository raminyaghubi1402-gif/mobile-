import unittest

from lili_trader.breakout_engine import BreakoutEngine


class BreakoutEngineTests(unittest.TestCase):
    def setUp(self):
        self.engine = BreakoutEngine()
        self.snapshot = {
            "spot_only": True,
            "regime": "bullish",
            "resistance_zone": {"kind": "resistance", "lower": 99.0, "upper": 100.0},
            "previous_5m": {
                "timestamp": 1_800_000_000, "fetched_at": 1_800_000_601,
                "open": 99.0, "high": 100.0, "low": 98.5, "close": 99.5,
            },
            "latest_5m": {
                "timestamp": 1_800_000_300, "fetched_at": 1_800_000_601,
                "open": 99.8, "high": 103.0, "low": 99.6, "close": 102.0,
            },
        }

    def test_fresh_body_close_above_resistance_emits_paper_signal(self):
        decision = self.engine.evaluate(self.snapshot)
        self.assertEqual(decision.status, "SIGNAL")
        self.assertEqual(decision.evidence["execution"], "paper_only")
        self.assertEqual(decision.evidence["stop_loss"], 99.0)
        self.assertAlmostEqual(decision.evidence["take_profit"], 106.755)

    def test_wick_only_breakout_is_rejected(self):
        self.snapshot["latest_5m"]["close"] = 100.0
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")

    def test_already_above_resistance_is_not_a_fresh_breakout(self):
        self.snapshot["previous_5m"]["close"] = 100.5
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")

    def test_non_consecutive_candles_are_rejected(self):
        self.snapshot["latest_5m"]["timestamp"] += 300
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")

    def test_unclosed_or_stale_candle_is_rejected(self):
        self.snapshot["latest_5m"]["fetched_at"] = 1_800_000_599
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")
        self.snapshot["latest_5m"]["fetched_at"] = 1_800_010_000
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")

    def test_spot_and_bullish_regime_are_required(self):
        self.snapshot["spot_only"] = False
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")
        self.snapshot["spot_only"] = True
        self.snapshot["regime"] = "range"
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")

    def test_missing_or_inverted_zone_fails_closed(self):
        self.snapshot["resistance_zone"] = None
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")
        self.snapshot["resistance_zone"] = {
            "kind": "resistance", "lower": 101.0, "upper": 100.0
        }
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")

    def test_bad_reward_risk_configuration_is_rejected(self):
        with self.assertRaises(ValueError):
            BreakoutEngine(minimum_reward_risk=1.2)


if __name__ == "__main__":
    unittest.main()
