import unittest

from lili_trader.volatility import VolatilityError, atr, atr_percent, true_ranges


class VolatilityTests(unittest.TestCase):
    def test_true_range_accounts_for_gaps(self):
        candles = [
            {"high": 12, "low": 10, "close": 11},
            {"high": 14, "low": 13, "close": 13.5},
            {"high": 13, "low": 9, "close": 10},
        ]
        self.assertEqual(true_ranges(candles), [2.0, 3.0, 4.5])

    def test_atr_simple_seed_and_wilder_smoothing(self):
        candles = [
            {"high": 12, "low": 10, "close": 11},
            {"high": 14, "low": 13, "close": 13.5},
            {"high": 13, "low": 9, "close": 10},
        ]
        # Seed=(2+3)/2=2.5; next Wilder value=(2.5+4.5)/2=3.5
        self.assertEqual(atr(candles, period=2), 3.5)

    def test_atr_percent_normalizes_by_latest_close(self):
        candles = [
            {"high": 12, "low": 10, "close": 11},
            {"high": 14, "low": 13, "close": 13.5},
            {"high": 13, "low": 9, "close": 10},
        ]
        self.assertEqual(atr_percent(candles, period=2), 35.0)

    def test_requires_enough_candles(self):
        with self.assertRaises(VolatilityError):
            atr([{"high": 12, "low": 10, "close": 11}], period=2)

    def test_rejects_invalid_ohlc(self):
        with self.assertRaises(VolatilityError):
            true_ranges([{"high": 9, "low": 10, "close": 10}])

    def test_rejects_nan_and_non_positive_values(self):
        with self.assertRaises(VolatilityError):
            true_ranges([{"high": float("nan"), "low": 10, "close": 10}])
        with self.assertRaises(VolatilityError):
            true_ranges([{"high": 10, "low": 0, "close": 5}])


if __name__ == "__main__":
    unittest.main()
