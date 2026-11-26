import unittest

from lili_trader.ict_engine import ICTEngine


class ICTEngineTests(unittest.TestCase):
    def setUp(self):
        self.engine = ICTEngine()
        start = 1_800_000_000
        self.reference = [
            {"timestamp": start + i * 300, "fetched_at": start + i * 300 + 300,
             "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
             "volume": 10.0}
            for i in range(20)
        ]
        # Sweep under 99 and reclaim; displacement breaks internal highs;
        # third candle confirms bullish FVG; fourth candle retests and reclaims.
        specs = [
            (99.5, 100.0, 98.0, 99.5),
            (99.5, 103.0, 99.4, 102.5),
            (102.5, 104.0, 101.0, 103.5),
            (102.0, 103.0, 100.5, 102.5),
        ]
        self.pattern = []
        for i, (o, h, l, c) in enumerate(specs, start=20):
            ts = start + i * 300
            self.pattern.append({
                "timestamp": ts, "fetched_at": ts + 300,
                "open": o, "high": h, "low": l, "close": c, "volume": 12.0,
            })
        self.snapshot = {
            "spot_only": True, "regime": "bullish", "now": start + 24 * 300,
            "reference_5m": self.reference,
            "liquidity_sweep_5m": self.pattern[0],
            "displacement_5m": self.pattern[1],
            "fvg_confirmation_5m": self.pattern[2],
            "retest_5m": self.pattern[3],
        }

    def test_complete_sweep_displacement_fvg_retest_signals(self):
        decision = self.engine.evaluate(self.snapshot)
        self.assertEqual(decision.status, "SIGNAL")
        self.assertEqual(decision.evidence["execution"], "paper_only")
        self.assertEqual(decision.evidence["stop_loss"], 98.0)
        self.assertEqual(decision.evidence["fvg_lower"], 100.0)
        self.assertEqual(decision.evidence["fvg_upper"], 101.0)
        self.assertAlmostEqual(decision.evidence["take_profit"], 109.50625)

    def test_no_sell_side_sweep_is_rejected(self):
        self.snapshot["liquidity_sweep_5m"]["low"] = 99.0
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")

    def test_no_bullish_displacement_is_rejected(self):
        self.snapshot["displacement_5m"]["close"] = 100.5
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")

    def test_missing_fvg_or_failed_retest_is_rejected(self):
        self.snapshot["fvg_confirmation_5m"]["low"] = 99.0
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")
        self.snapshot["fvg_confirmation_5m"]["low"] = 101.0
        self.snapshot["retest_5m"]["close"] = 100.5
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")

    def test_gaps_stale_unclosed_and_non_bullish_are_rejected(self):
        self.snapshot["retest_5m"]["timestamp"] += 300
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")
        self.snapshot["retest_5m"]["timestamp"] -= 300
        self.snapshot["retest_5m"]["fetched_at"] -= 1
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")
        self.snapshot["retest_5m"]["fetched_at"] = self.snapshot["retest_5m"]["timestamp"] + 300
        self.snapshot["now"] -= 901
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")
        self.snapshot["now"] += 901
        self.snapshot["regime"] = "range"
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")

    def test_non_spot_is_rejected(self):
        self.snapshot["spot_only"] = False
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")


if __name__ == "__main__":
    unittest.main()
