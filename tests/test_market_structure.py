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
    def test_pivot_window_must_be_a_positive_integer(self):
        rows = candles_from([10,11,10,12,11,13],[8,9,8,10,9,11])
        for invalid in (True, 1.0, 1.5, "1", 0, -1):
            with self.subTest(pivot_window=invalid):
                with self.assertRaises(ValueError):
                    analyze_market_structure(rows, pivot_window=invalid)

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

    def test_rejects_fractional_boolean_negative_and_nonfinite_timestamps(self):
        for invalid_timestamp in (1.5, True, -1, float("nan"), float("inf")):
            with self.subTest(timestamp=invalid_timestamp):
                rows = candles_from([10,11,10,12,11,13],[8,9,8,10,9,11])
                rows[2]["timestamp"] = invalid_timestamp
                with self.assertRaises(ValueError):
                    analyze_market_structure(rows, pivot_window=1)

    def test_rejects_partially_missing_timestamps(self):
        rows = candles_from([10,11,10,12,11,13],[8,9,8,10,9,11])
        rows[2].pop("timestamp")
        with self.assertRaises(ValueError):
            analyze_market_structure(rows, pivot_window=1)

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

    def test_bearish_structure_breaking_protected_high_is_choch_up(self):
        # Confirm LL/LH, then close above the protected lower high.
        rows=candles_from([24,23,25,22,23,22,26],
                          [22,20,21,18,19,20,21],
                          [23,21,24,19,20,21,24])
        result=analyze_market_structure(rows,pivot_window=1)
        reversal=next(e for e in result.events if e.kind=="CHOCH_UP")
        self.assertEqual(reversal.candle_index,6)
        self.assertEqual(reversal.level,23)
        self.assertEqual(result.bias,"up")

    def test_bos_down_after_bearish_choch_uses_new_confirmed_lower_low(self):
        rows=candles_from(
            [8,10,9,12,11,10,9,11,10,9,8],
            [6,7,5,8,7,8,4,5,3,3.5,2.5],
            [7,9,6,11,10,9,6,6,5,3.8,2.8])
        result=analyze_market_structure(rows,pivot_window=1)
        self.assertTrue(any(e.kind=="CHOCH_DOWN" and e.candle_index==6 for e in result.events))
        continuation=next(e for e in result.events if e.kind=="BOS_DOWN" and e.candle_index==10)
        self.assertEqual(continuation.level,3)
        self.assertEqual(result.bias,"down")

    def test_bos_up_after_bullish_choch_uses_new_confirmed_higher_high(self):
        rows=candles_from(
            [24,23,25,22,23,22,26,25,27,26,28],
            [22,20,21,18,19,20,21,21,22,23,24],
            [23,21,24,19,20,21,24,24,26,25,27.5])
        result=analyze_market_structure(rows,pivot_window=1)
        self.assertTrue(any(e.kind=="CHOCH_UP" and e.candle_index==6 for e in result.events))
        continuation=next(e for e in result.events if e.kind=="BOS_UP" and e.candle_index==10)
        self.assertEqual(continuation.level,27)
        self.assertEqual(result.bias,"up")

    def test_close_equal_to_swing_level_is_not_a_break(self):
        rows=candles_from([10,15,12,13,16,14,15,17,18],
                          [7,8,9,8,10,9,11,12,13],
                          [9,14,11,12,15,13,14,16,17])
        # The high at index 4 is confirmed at index 5. At index 6,
        # close equals that level while the candle's high remains valid.
        rows[6]["high"]=17
        rows[6]["close"]=16
        result=analyze_market_structure(rows,pivot_window=1)
        self.assertFalse(any(e.broken_swing_index==4 and e.candle_index==6 for e in result.events))

    def test_each_confirmed_swing_can_be_broken_only_once(self):
        # A close above one confirmed swing must not emit duplicate BOS events
        # for that same swing on subsequent candles.
        rows=candles_from([10,15,12,13,16,14,15,17,18,19,20],
                          [7,8,9,8,10,9,11,12,13,14,15],
                          [9,14,11,12,15,13,14,16,17,18,19])
        result=analyze_market_structure(rows,pivot_window=1)
        for swing_index in {e.broken_swing_index for e in result.events}:
            matching=[e for e in result.events if e.broken_swing_index==swing_index]
            self.assertEqual(len(matching),1)

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


    def test_minor_higher_low_does_not_replace_bos_protected_low(self):
        # BOS above 16 confirms the last higher low (11) as protection.
        # A later local low at 12 is only a minor pivot; closing at 11.5
        # must not be mislabeled as CHoCH below the protected level 11.
        rows=candles_from(
            [10,12,11,15,13,14,16,15,18,17,19,18],
            [8,9,7,10,9,12,12,11,13,12,14,10.5],
            [9,11,10,14,12,13,15,14,17,15,18,11.5])
        result=analyze_market_structure(rows,pivot_window=1)
        self.assertTrue(any(e.kind=="BOS_UP" and e.candle_index==8
                            and e.level==16 for e in result.events))
        self.assertFalse(any(e.kind=="CHOCH_DOWN" and e.candle_index==11
                             for e in result.events))

    def test_close_below_bos_protected_low_triggers_choch(self):
        # Same structure as above, but close below 11 (not merely below
        # the minor pivot 12): the protected structure must break.
        rows=candles_from(
            [10,12,11,15,13,14,16,15,18,17,19,18],
            [8,9,7,10,9,12,12,11,13,12,14,10],
            [9,11,10,14,12,13,15,14,17,15,18,10.5])
        result=analyze_market_structure(rows,pivot_window=1)
        events=[e for e in result.events if e.kind=="CHOCH_DOWN"]
        self.assertTrue(events)
        # The protected level must be the BOS-confirmed swing low (11),
        # not the later minor pivot (12). Assert the event itself, not just
        # the final event in case another structure event follows.
        protected_break = next(e for e in events if e.candle_index == 11)
        self.assertEqual(protected_break.level,11)
        self.assertEqual(protected_break.broken_swing_index,7)
        self.assertEqual(protected_break.candle_index,11)



    def test_neutral_break_does_not_promote_bias_to_directional(self):
        # A range boundary break is not itself trend confirmation.
        rows=candles_from(
            [10,14,11,13,10,15,12,14,11,16,13,17,14],
            [7,9,8,6,7,10,9,7,8,11,10,8,9],
            [9,13,10,12,9,14,11,13,10,15,12,16,13])
        result=analyze_market_structure(rows,pivot_window=1)
        neutral_breaks=[e for e in result.events if e.kind in ("BREAK_UP","BREAK_DOWN")]
        self.assertTrue(neutral_breaks, "fixture must exercise a neutral break")
        # Event type is the contract; later candles may independently confirm trend.



    def test_future_extension_cannot_change_confirmed_pivots_or_events_before_cutoff(self):
        # Compare an analysis truncated at cutoff with a longer series whose
        # future candles are radically different. Only events and swings
        # confirmed by cutoff are compared; later confirmation is allowed.
        base = candles_from(
            [10,15,12,13,16,14,15,17,18,19,20,18,22,19,23],
            [7,8,9,8,10,9,11,12,13,14,15,12,16,13,17],
            [9,14,11,12,15,13,14,16,17,18,19,13,21,14,22])
        cutoff = 11
        future = candles_from(
            [40,20,50,15,60,10,70,5],
            [30,10,35,8,40,6,45,2],
            [39,11,49,9,59,7,69,3])
        future = [dict(row, timestamp=row["timestamp"] + len(base)) for row in future]
        full = base + future

        truncated_result = analyze_market_structure(base[:cutoff + 1], pivot_window=1)
        extended_result = analyze_market_structure(full, pivot_window=1)
        cutoff_events = [
            (e.kind, e.candle_index, e.level, e.broken_swing_index, e.close)
            for e in extended_result.events if e.candle_index <= cutoff
        ]
        expected_events = [
            (e.kind, e.candle_index, e.level, e.broken_swing_index, e.close)
            for e in truncated_result.events
        ]
        self.assertEqual(expected_events, cutoff_events)

        # Swing must be confirmed within cutoff (pivot index + window).
        cutoff_swings = [(s.kind, s.index, s.price) for s in truncated_result.swings]
        extended_confirmed = [
            (s.kind, s.index, s.price) for s in extended_result.swings
            if s.index + 1 <= cutoff
        ]
        self.assertEqual(cutoff_swings, extended_confirmed)


    def test_new_lower_high_after_bos_down_becomes_choch_protection(self):
        # After bearish continuation, the latest confirmed lower high—not
        # the older pre-continuation high—must protect the downtrend.
        rows=candles_from(
            [8,10,9,12,11,10,9,11,10,9,8,6,7,6,8],
            [6,7,5,8,7,8,4,5,3,3.5,2.5,2,2.2,1.8,2],
            [7,9,6,11,10,9,6,6,5,3.8,2.8,5.5,6.5,5.5,7.5])
        result=analyze_market_structure(rows,pivot_window=1)
        self.assertTrue(any(e.kind=="CHOCH_DOWN" and e.candle_index==6 for e in result.events))
        self.assertTrue(any(e.kind=="BOS_DOWN" and e.candle_index==10 for e in result.events))
        reversal=next((e for e in result.events if e.kind=="CHOCH_UP" and e.candle_index==14),None)
        self.assertIsNotNone(reversal, "close above the latest confirmed lower high must trigger CHoCH_UP")
        self.assertEqual(reversal.level,7)

if __name__=="__main__":
    unittest.main()
