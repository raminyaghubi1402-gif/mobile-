import unittest
from lili_trader.market_structure import analyze_market_structure

def candles_from(highs, lows, closes=None):
    rows=[]
    for i,(h,l) in enumerate(zip(highs,lows)):
        close=closes[i] if closes else (h+l)/2
        open_=(h+l)/2
        rows.append({"timestamp":i,"open":open_,"high":h,"low":l,"close":close})
    return rows

class MarketStructureTests(unittest.TestCase):
    def test_insufficient_data_is_unknown(self):
        result=analyze_market_structure(candles_from([10,11,10],[8,9,8]),pivot_window=1)
        self.assertEqual(result.bias,"unknown")
        self.assertIsNone(result.last_event)

    def test_uptrend_close_break_is_bos(self):
        rows=candles_from([10,15,12,13,16,14,15,17,18,19,20],
                          [7,8,9,8,10,9,11,12,13,14,15],
                          [9,14,11,12,15,13,14,16,17,18,19])
        result=analyze_market_structure(rows,pivot_window=1)
        self.assertTrue(any(e.kind=="BOS_UP" for e in result.events))

    def test_downtrend_close_break_is_bos_down(self):
        rows=candles_from([20,18,19,17,18,16,17,15,14,13,12],
                          [15,10,13,9,12,8,11,7,6,5,4],
                          [18,11,17,10,16,9,15,8,7,6,5])
        result=analyze_market_structure(rows,pivot_window=1)
        self.assertTrue(any(e.kind=="BOS_DOWN" for e in result.events))

    def test_wick_only_break_does_not_count(self):
        rows=candles_from([10,15,12,13,16,14,15,17,18,19,20],
                          [7,8,9,8,10,9,11,12,13,14,15],
                          [9,14,11,12,15,13,14,16,17,18,19])
        rows[-1]["high"]=25
        rows[-1]["close"]=19
        result=analyze_market_structure(rows,pivot_window=1)
        self.assertFalse(any(e.kind=="BOS_UP" and e.candle_index==len(rows)-1 for e in result.events))

    def test_invalid_ohlc_rejected(self):
        rows=candles_from([10,11,10,12,11,13],[8,9,8,10,9,11])
        rows[2]["low"]=rows[2]["high"]+1
        with self.assertRaises(ValueError):
            analyze_market_structure(rows,pivot_window=1)

    def test_nonchronological_timestamps_rejected(self):
        rows=candles_from([10,11,10,12,11,13],[8,9,8,10,9,11])
        rows[3]["timestamp"]=rows[2]["timestamp"]
        with self.assertRaises(ValueError):
            analyze_market_structure(rows,pivot_window=1)

    def test_pivot_not_available_before_confirmation(self):
        # High at index 3 needs one right-side candle: only visible at index 4.
        rows=candles_from([10,12,11,20,13,14,15],
                          [5,6,7,8,9,10,11],
                          [9,11,10,19,12,13,14])
        result=analyze_market_structure(rows,pivot_window=1)
        swing=next(s for s in result.swings if s.kind=="high" and s.index==3)
        # It must not generate a break on the pivot candle itself.
        self.assertFalse(any(e.broken_swing_index==3 and e.candle_index<=4 for e in result.events))


    def test_bullish_structure_breaking_protected_low_is_choch_down(self):
        # Confirm HH/HL first, then close below the protected higher low.
        rows=candles_from([8,10,9,12,11,10,9],
                          [6,7,5,8,7,8,4],
                          [7,9,6,11,10,9,6])
        result=analyze_market_structure(rows,pivot_window=1)
        reversal=next(e for e in result.events if e.kind=="CHOCH_DOWN")
        self.assertEqual(reversal.candle_index,6)
        self.assertEqual(reversal.level,7)
        self.assertEqual(result.bias,"down")

    def test_changing_future_candles_does_not_rewrite_past_events(self):
        base=candles_from([10,15,12,13,16,14,15,17,18],
                          [7,8,9,8,10,9,11,12,13],
                          [9,14,11,12,15,13,14,16,17])
        changed=[dict(c) for c in base]
        # Mutate only the future tail; already-recorded event chronology must remain.
        tail=candles_from([30,31,32],[20,21,22],[29,30,31])
        for row in tail:
            row["timestamp"] += len(base)
        changed.extend(tail)
        a=analyze_market_structure(base,pivot_window=1)
        b=analyze_market_structure(changed,pivot_window=1)
        past_a=[(e.kind,e.candle_index,e.level,e.broken_swing_index) for e in a.events]
        past_b=[(e.kind,e.candle_index,e.level,e.broken_swing_index) for e in b.events if e.candle_index<len(base)]
        self.assertEqual(past_a,past_b)

if __name__=="__main__":
    unittest.main()
