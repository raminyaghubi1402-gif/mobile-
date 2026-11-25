import unittest

from lili_trader.breakout_retest_engine import BreakoutRetestEngine


class BreakoutRetestEngineTests(unittest.TestCase):
    def setUp(self):
        self.engine = BreakoutRetestEngine()
        base = 1_800_000_000
        self.snapshot = {
            "spot_only": True,
            "regime": "bullish",
            "resistance_zone": {"kind": "resistance", "lower": 98.0, "upper": 100.0},
            "pre_breakout_5m": {
                "timestamp": base, "fetched_at": base + 900,
                "open": 98.5, "high": 100.0, "low": 98.0, "close": 99.5,
            },
            "breakout_5m": {
                "timestamp": base + 300, "fetched_at": base + 900,
                "open": 99.5, "high": 103.0, "low": 99.2, "close": 102.0,
            },
            "retest_5m": {
                "timestamp": base + 600, "fetched_at": base + 900,
                "open": 100.2, "high": 102.5, "low": 99.8, "close": 101.5,
            },
        }

    def test_confirmed_breakout_retest_emits_paper_signal_and_levels(self):
        decision = self.engine.evaluate(self.snapshot)
        self.assertEqual(decision.status, "SIGNAL")
        self.assertEqual(decision.evidence["execution"], "paper_only")
        self.assertEqual(decision.evidence["entry_reference"], 101.5)
        self.assertEqual(decision.evidence["stop_loss"], 98.0)
        self.assertAlmostEqual(decision.evidence["take_profit"], 107.00375)

    def test_no_touch_of_old_resistance_means_wait(self):
        self.snapshot["retest_5m"]["low"] = 100.1
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")

    def test_retest_close_back_below_zone_means_wait(self):
        self.snapshot["retest_5m"]["close"] = 99.9
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")

    def test_wick_only_initial_breakout_is_rejected(self):
        self.snapshot["breakout_5m"]["close"] = 100.0
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")

    def test_retest_through_entire_zone_is_rejected(self):
        self.snapshot["retest_5m"]["low"] = 97.9
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")

    def test_breakout_must_be_fresh(self):
        self.snapshot["pre_breakout_5m"]["close"] = 100.1
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")

    def test_all_three_candles_must_be_consecutive(self):
        self.snapshot["retest_5m"]["timestamp"] += 300
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")

    def test_unclosed_or_stale_retest_is_rejected(self):
        self.snapshot["retest_5m"]["fetched_at"] -= 1
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")
        self.snapshot["retest_5m"]["fetched_at"] = 1_800_010_000
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")

    def test_only_spot_bullish_regime_is_allowed(self):
        self.snapshot["spot_only"] = False
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")
        self.snapshot["spot_only"] = True
        self.snapshot["regime"] = "range"
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")

    def test_invalid_zone_and_ohlc_fail_closed(self):
        self.snapshot["resistance_zone"] = {
            "kind": "resistance", "lower": 101.0, "upper": 100.0
        }
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")
        self.snapshot["resistance_zone"] = {
            "kind": "resistance", "lower": 98.0, "upper": 100.0
        }
        self.snapshot["retest_5m"]["low"] = 103.0
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")

    def test_minimum_reward_risk_cannot_be_lowered_below_policy(self):
        with self.assertRaises(ValueError):
            BreakoutRetestEngine(minimum_reward_risk=1.2)


if __name__ == "__main__":
    unittest.main()
