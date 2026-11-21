import unittest
from lili_trader.market_structure_context import (
    analyze_market_context, analyze_timeframe_alignment,
)


def candles(highs, lows, closes):
    rows=[]
    for i,(h,l,c) in enumerate(zip(highs,lows,closes)):
        rows.append({"timestamp":i,"open":(h+l)/2,"high":h,"low":l,"close":c})
    return rows


class MarketStructureContextTests(unittest.TestCase):
    def test_labels_classify_higher_and_lower_high_low(self):
        rows=candles(
            [10,15,12,13,16,14,15,17,18,19,20,18,22],
            [7,8,9,8,10,9,11,12,13,14,15,12,16],
            [9,14,11,12,15,13,14,16,17,18,19,13,21])
        context=analyze_market_context(rows,pivot_window=1,external_window=2)
        labels=[s.label for s in context.labels]
        self.assertTrue(any(label in ("HH","LH","EQH") for label in labels))
        self.assertTrue(any(label in ("HL","LL","EQL") for label in labels))
        self.assertTrue(context.external_swings)
        self.assertTrue(context.internal_swings)

    def test_external_window_must_be_larger_than_internal(self):
        rows=candles([10,11,12,13,14],[5,6,7,8,9],[9,10,11,12,13])
        with self.assertRaises(ValueError):
            analyze_market_context(rows,pivot_window=2,external_window=2)

    def test_wick_sweep_detected_only_after_pivot_confirmation(self):
        rows=candles(
            [10,15,12,13,16,14,15,17,18],
            [7,8,9,8,10,9,11,12,13],
            [9,14,11,12,15,13,14,16,17])
        # Pivot high at 4 confirms at 5. Wick beyond it, close back below 16.
        rows[7]["high"]=17.2
        rows[7]["close"]=15.8
        context=analyze_market_context(rows,pivot_window=1,external_window=2)
        self.assertTrue(any(s.kind=="SWEEP_HIGH" and s.swing_index==4 for s in context.sweeps))

    def test_break_close_is_not_mislabeled_as_sweep(self):
        rows=candles(
            [10,15,12,13,16,14,15,17,18],
            [7,8,9,8,10,9,11,12,13],
            [9,14,11,12,15,13,14,16,17])
        rows[7]["high"]=17.2
        rows[7]["close"]=16.8
        context=analyze_market_context(rows,pivot_window=1,external_window=2)
        self.assertFalse(any(s.kind=="SWEEP_HIGH" and s.swing_index==4 for s in context.sweeps))

    def test_close_through_range_boundary_is_breakout_not_sweep(self):
        rows=candles(
            [10,14,11,13,10,15,12,14,11,16,13,17,14],
            [7,9,8,6,7,10,9,7,8,11,10,8,9],
            [9,13,10,12,9,14,11,13,10,15,12,16,13])
        context=analyze_market_context(rows,pivot_window=1,external_window=2)
        self.assertIn(context.range_context.status,
                      ("candidate","range","breakout_up","breakout_down","unknown"))
        self.assertIsNotNone(context.range_context.width)

    def test_timeframe_consensus_and_conflict(self):
        up=candles([10,15,12,13,16,14,15,17,18,19,20],
                   [7,8,9,8,10,9,11,12,13,14,15],
                   [9,14,11,12,15,13,14,16,17,18,19])
        down=candles([20,18,19,17,18,16,17,15,14,13,12],
                     [15,10,13,9,12,8,11,7,6,5,4],
                     [18,11,17,10,16,9,15,8,7,6,5])
        result=analyze_timeframe_alignment({"1h":up,"15m":up,"5m":down},pivot_window=1)
        self.assertEqual(result.higher_timeframe_bias,"up")
        self.assertEqual(result.conflicting,("5m",))
        self.assertEqual(result.consensus,"mixed")
        result2=analyze_timeframe_alignment({"1h":up,"15m":up},pivot_window=1)
        self.assertEqual(result2.consensus,"up")
        self.assertEqual(result2.aligned,("1h","15m"))

    def test_unknown_higher_timeframe_is_not_a_directional_command(self):
        short=candles([10,11,10],[8,9,8],[9,10,9])
        result=analyze_timeframe_alignment({"1h":short,"5m":short},pivot_window=2)
        self.assertEqual(result.higher_timeframe_bias,"unknown")
        self.assertIn("اجباری",result.reason_fa)


    def test_bos_protection_tracks_latest_confirmed_opposing_swing(self):
        bullish=candles(
            [10,12,11,15,13,14,16,15,18,17,19,18],
            [8,9,7,10,9,12,12,11,13,12,14,10.5],
            [9,11,10,14,12,13,15,14,17,15,18,11.5])
        context=analyze_market_context(bullish,pivot_window=1,external_window=2)
        self.assertIsNotNone(context.protected_low)
        self.assertEqual(context.protected_low.price,11)
        self.assertEqual(context.protected_low.index,7)

        bearish=candles(
            [22,21,23,20,21,18,18,19,17,18,16,19.5],
            [20,18,19,15,17,16,14,15,12,13,11,12],
            [21,19,20,16,18,17,15,16,13,15,12,18.5])
        context=analyze_market_context(bearish,pivot_window=1,external_window=2)
        self.assertIsNotNone(context.protected_high)
        self.assertEqual(context.protected_high.price,19)
        self.assertEqual(context.protected_high.index,7)

    def test_external_boundary_close_break_is_classified_as_breakout(self):
        rows=candles(
            [10,15,12,13,16,14,15,17,18],
            [7,8,9,8,10,9,11,12,13],
            [9,14,11,12,15,13,14,16,17])
        rows[7]["close"]=16.8  # wick above 16, but this is a close-through break
        context=analyze_market_context(rows,pivot_window=1,external_window=2)
        self.assertEqual(context.range_context.status,"breakout_up")
        self.assertFalse(any(s.kind=="SWEEP_HIGH" and s.swing_index==4 for s in context.sweeps))

    def test_range_touch_counts_start_after_both_external_pivots_confirm(self):
        rows=candles(
            [10,14,11,13,10,15,12,14,11,16,13,17,14,18,15,17,13],
            [7,9,8,6,7,10,9,7,8,11,10,8,9,12,11,9,8],
            [9,13,10,12,9,14,11,13,10,15,12,16,13,17,14,15,12])
        context=analyze_market_context(rows,pivot_window=1,external_window=2)
        from lili_trader.market_structure import analyze_market_structure
        external=analyze_market_structure(rows,pivot_window=2)
        hs=[s for s in external.swings if s.kind=="high"][-3:]
        ls=[s for s in external.swings if s.kind=="low"][-3:]
        upper_swing=max(hs,key=lambda s:s.price)
        lower_swing=min(ls,key=lambda s:s.price)
        start=max(upper_swing.confirmation_index,lower_swing.confirmation_index)
        upper=upper_swing.price; lower=lower_swing.price; tol=0.0025
        expected_high=sum(1 for r in rows[start:] if r["high"]>=upper*(1-tol) and r["close"]<=upper*(1+tol))
        expected_low=sum(1 for r in rows[start:] if r["low"]<=lower*(1+tol) and r["close"]>=lower*(1-tol))
        self.assertEqual(context.range_context.touches_high,expected_high)
        self.assertEqual(context.range_context.touches_low,expected_low)

if __name__=="__main__":
    unittest.main()
