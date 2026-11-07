import unittest
from lili_trader.market_analyst import analyze_market


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


if __name__ == "__main__":
    unittest.main()
