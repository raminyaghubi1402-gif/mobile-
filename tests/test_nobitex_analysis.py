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
        self.assertIn("داده ناکافی", report)

    def test_open_candle_is_excluded_from_indicator_calculation(self):
        # The candle opened at 10:00 and was fetched at 10:30: it is not closed.
        candle = NobitexCandle(
            "BTCIRT", "60", 36000, 100, 110, 90, 105, 10,
            fetched_at=37800,
        )
        report = format_nobitex_report([candle])
        self.assertIn("هنوز کندل بسته‌شده‌ای", report)

    def test_closed_candles_report_tehran_time_and_live_candle_warning(self):
        first = NobitexCandle(
            "BTCIRT", "60", 36000, 100, 110, 90, 105, 10,
            fetched_at=43200,
        )
        current = NobitexCandle(
            "BTCIRT", "60", 39600, 105, 112, 100, 108, 12,
            fetched_at=42000,
        )
        report = format_nobitex_report([first, current])
        self.assertIn("زمان آخرین کندل بسته‌شده", report)
        self.assertIn("کندل جاری و بسته‌نشده از محاسبات حذف شد", report)
        self.assertIn("تهران", report)
        self.assertIn("کندل بسته‌شدهٔ بررسی‌شده: 1", report)


if __name__ == "__main__":
    unittest.main()
