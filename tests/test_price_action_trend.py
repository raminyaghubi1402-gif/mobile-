import unittest
from lili_trader.price_action_trend import classify_price_action_trend

def make_trending(direction):
    if direction == "up":
        highs=[10,12,11,14,13,16,15,18,17,20,19,22]
        lows=[8,9,8.5,11,10.5,13,12.5,15,14.5,17,16.5,19]
    else:
        highs=[22,20,21,18,19,16,17,14,15,12,13,10]
        lows=[20,18,19,16,17,14,15,12,13,10,11,8]
    return [{"timestamp":i,"open":lo+(hi-lo)*.4,"high":hi,"low":lo,
             "close":lo+(hi-lo)*.6} for i,(hi,lo) in enumerate(zip(highs,lows))]

class PriceActionTrendTests(unittest.TestCase):
    def test_higher_highs_and_higher_lows_are_bullish(self):
        self.assertEqual(classify_price_action_trend(make_trending("up"),pivot_window=1).direction,"up")
    def test_lower_highs_and_lower_lows_are_bearish(self):
        self.assertEqual(classify_price_action_trend(make_trending("down"),pivot_window=1).direction,"down")
    def test_insufficient_data_fails_closed(self):
        self.assertEqual(classify_price_action_trend(make_trending("up")[:5]).direction,"unknown")
    def test_invalid_ohlc_is_rejected(self):
        rows=make_trending("up"); rows[3]["low"]=rows[3]["high"]+1
        with self.assertRaises(ValueError): classify_price_action_trend(rows,pivot_window=1)
    def test_mixed_structure_is_not_trend(self):
        rows=make_trending("up")
        rows[-3].update(high=9,low=2,open=4,close=5)
        self.assertNotEqual(classify_price_action_trend(rows,pivot_window=1).direction,"up")

if __name__ == "__main__": unittest.main()
