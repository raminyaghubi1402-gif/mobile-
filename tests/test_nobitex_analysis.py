import unittest
from lili_trader.technical_analysis import analyze_closes
from lili_trader.nobitex_report import format_nobitex_report
from lili_trader.nobitex_data import NobitexCandle


class TechnicalAnalysisTests(unittest.TestCase):
    def test_insufficient_data_is_not_overclaimed(self):
        result = analyze_closes([100, 101, 102])
        self.assertEqual(result.trend, "داده ناکافی")
        self.assertIsNone(result.rsi14)

    def test_upward_series_trend(self):
        result = analyze_closes(list(range(100, 130)))
        self.assertEqual(result.trend, "تمایل صعودی")
        self.assertIsNotNone(result.rsi14)

    def test_invalid_prices_rejected(self):
        with self.assertRaises(ValueError):
            analyze_closes([100, 0, 101])


class NobitexReportTests(unittest.TestCase):
    def test_report_is_persian_and_disclaims_trade(self):
        candle = NobitexCandle("BTCIRT", "60", 1700000000, 100, 110, 90, 105, 10)
        report = format_nobitex_report([candle])
        self.assertIn("نوبیتکس", report)
        self.assertIn("توصیهٔ خرید/فروش نیست", report)
        self.assertIn("BTCIRT", report)


if __name__ == "__main__":
    unittest.main()
