import unittest

from lili_trader.analysis import Candle, analyze_candle
from lili_trader.indicators import ema, rsi, sma


class IndicatorTests(unittest.TestCase):
    def test_sma(self):
        self.assertEqual(sma([1, 2, 3, 4], 3), 3)

    def test_sma_rejects_short_input(self):
        with self.assertRaises(ValueError):
            sma([1, 2], 3)

    def test_ema_single_value(self):
        self.assertEqual(ema([5], 3), 5)

    def test_rsi_uptrend(self):
        self.assertEqual(rsi([1, 2, 3, 4, 5], 4), 100)

    def test_candle_wicks_and_direction(self):
        result = analyze_candle(Candle(open=10, high=14, low=8, close=12))
        self.assertEqual(result["direction"], "صعودی")
        self.assertEqual(result["upper_wick"], 2)
        self.assertEqual(result["lower_wick"], 2)

    def test_invalid_candle(self):
        with self.assertRaises(ValueError):
            analyze_candle(Candle(open=10, high=8, low=9, close=10))


if __name__ == "__main__":
    unittest.main()
