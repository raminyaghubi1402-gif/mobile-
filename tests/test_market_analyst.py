import unittest
from lili_trader.market_analyst import analyze_market


def candles_for(direction="up", count=25):
    if direction == "up":
        centers = [10, 8, 12, 9, 14, 11, 16, 13, 18, 15, 20, 17, 22,
                   19, 24, 21, 26, 23, 28, 25, 30, 27, 32, 29, 34]
    elif direction == "down":
        centers = [34, 36, 32, 35, 30, 33, 28, 31, 26, 29, 24, 27, 22,
                   25, 20, 23, 18, 21, 16, 19, 14, 17, 12, 15, 10]
    else:
        # Last confirmed highs fall while lows rise: mixed/ranging structure.
        centers = [10, 8, 13, 7, 12, 8, 14, 9, 13, 10, 12, 11, 11,
                   12, 10, 13, 9, 14, 8, 15, 7, 16, 6, 17, 5]
    centers = centers[:count]
    return [
        {"timestamp": i, "open": center, "high": center + 0.3,
         "low": center - 0.3, "close": center}
        for i, center in enumerate(centers)
    ]


class ProfessionalMarketAnalystTests(unittest.TestCase):
    def test_missing_required_timeframes_fails_closed(self):
        result = analyze_market("BTCIRT", {"1h": [], "15m": [], "5m": []})
        self.assertEqual(result.decision, "WAIT")
        self.assertIsNone(result.direction)
        self.assertIn("داده", result.report_fa)
        self.assertIn("هیچ معامله‌ای اجرا نمی‌شود", result.report_fa)

    def test_rejects_empty_symbol(self):
        with self.assertRaises(ValueError):
            analyze_market(" ", {})

    def test_rejects_non_mapping_input(self):
        with self.assertRaises(TypeError):
            analyze_market("BTCIRT", [])

    def test_aligned_bullish_structure_allows_buy_scenario_only(self):
        up = candles_for("up")
        result = analyze_market("BTCIRT", {"1h": up, "15m": up, "5m": up}, pivot_window=1)
        self.assertEqual(result.decision, "READY_BUY")
        self.assertEqual(result.direction, "up")
        self.assertTrue(result.gate.allowed)
        self.assertTrue(all("sell" not in item.name for item in result.candidates))

    def test_aligned_bearish_structure_allows_sell_scenario_only(self):
        down = candles_for("down")
        result = analyze_market("BTCIRT", {"1h": down, "15m": down, "5m": down}, pivot_window=1)
        self.assertEqual(result.decision, "READY_SELL")
        self.assertEqual(result.direction, "down")
        self.assertTrue(result.gate.allowed)
        self.assertTrue(all("buy" not in item.name for item in result.candidates))

    def test_range_market_never_generates_entry(self):
        sideways = candles_for("range")
        result = analyze_market("BTCIRT", {"1h": sideways, "15m": sideways, "5m": sideways}, pivot_window=1)
        self.assertEqual(result.decision, "WAIT")
        self.assertFalse(result.gate.allowed)

    def test_opposite_five_minute_trigger_waits_instead_of_reversing(self):
        up, down = candles_for("up"), candles_for("down")
        result = analyze_market("BTCIRT", {"1h": up, "15m": up, "5m": down}, pivot_window=1)
        self.assertEqual(result.decision, "WAIT")
        self.assertEqual(result.direction, "up")
        self.assertEqual(result.gate.status, "wait_trigger")

    def test_insufficient_hourly_history_never_becomes_ready(self):
        up = candles_for("up", count=12)
        result = analyze_market("BTCIRT", {"1h": up, "15m": up, "5m": up}, pivot_window=1)
        self.assertEqual(result.decision, "WAIT")
        self.assertEqual(result.regime.trend, "unknown")


if __name__ == "__main__":
    unittest.main()
