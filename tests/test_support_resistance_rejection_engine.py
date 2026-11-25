import unittest

from lili_trader.support_resistance_rejection_engine import SupportResistanceRejectionEngine


class SupportResistanceRejectionEngineTests(unittest.TestCase):
    def setUp(self):
        self.engine = SupportResistanceRejectionEngine()
        base = 1_800_000_000
        self.snapshot = {
            "spot_only": True,
            "regime": "bullish",
            "support_zone": {"kind": "support", "lower": 98.0, "upper": 100.0},
            "previous_5m": {
                "timestamp": base, "fetched_at": base + 600,
                "open": 101.0, "high": 103.0, "low": 100.5, "close": 101.5,
            },
            "latest_5m": {
                "timestamp": base + 300, "fetched_at": base + 600,
                "open": 99.5, "high": 102.0, "low": 99.0, "close": 101.0,
            },
        }

    def test_valid_support_rejection_emits_paper_signal(self):
        decision = self.engine.evaluate(self.snapshot)
        self.assertEqual(decision.status, "SIGNAL")
        self.assertEqual(decision.evidence["execution"], "paper_only")
        self.assertEqual(decision.evidence["entry_reference"], 101.0)
        self.assertEqual(decision.evidence["stop_loss"], 98.0)
        self.assertEqual(decision.evidence["take_profit"], 105.5)

    def test_no_support_touch_means_wait(self):
        self.snapshot["latest_5m"]["low"] = 100.1
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")

    def test_weak_or_bearish_close_means_wait(self):
        self.snapshot["latest_5m"]["close"] = 99.9
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")

    def test_support_zone_full_break_is_rejected(self):
        self.snapshot["latest_5m"]["low"] = 97.9
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")

    def test_previous_price_must_approach_from_above(self):
        self.snapshot["previous_5m"]["close"] = 99.0
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")

    def test_candles_must_be_consecutive_closed_and_fresh(self):
        self.snapshot["latest_5m"]["timestamp"] += 300
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")
        self.snapshot["latest_5m"]["timestamp"] -= 300
        self.snapshot["latest_5m"]["fetched_at"] -= 301
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")

    def test_only_spot_bullish_regime_is_allowed(self):
        self.snapshot["spot_only"] = False
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")
        self.snapshot["spot_only"] = True
        self.snapshot["regime"] = "range"
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")

    def test_invalid_zone_and_ohlc_fail_closed(self):
        self.snapshot["support_zone"] = {
            "kind": "support", "lower": 101.0, "upper": 100.0
        }
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")
        self.snapshot["support_zone"] = {
            "kind": "support", "lower": 98.0, "upper": 100.0
        }
        self.snapshot["latest_5m"]["low"] = 103.0
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")

    def test_minimum_reward_risk_cannot_be_lowered_below_policy(self):
        with self.assertRaises(ValueError):
            SupportResistanceRejectionEngine(minimum_reward_risk=1.2)


if __name__ == "__main__":
    unittest.main()
