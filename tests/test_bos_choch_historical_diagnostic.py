import unittest
from scripts.bos_choch_historical_diagnostic import outcome_stats, quality, prefix_causality, closed_prefix

def candles(closes,step=300):
    return [{"timestamp":i*step,"open":c,"high":c+1,"low":c-1,"close":c,"volume":1.0} for i,c in enumerate(closes)]

class HistoricalAuditTests(unittest.TestCase):
    def test_quality_flags_duplicate_and_invalid_candle(self):
        rows=candles([10,11,12]); rows[1]["timestamp"]=rows[0]["timestamp"]; rows[2]["high"]=rows[2]["low"]-1
        q=quality(rows,300); self.assertEqual(q[0],1); self.assertEqual(q[1],1); self.assertEqual(q[3],1)

    def test_forward_failure_is_level_reclaim_not_subjective_false_signal(self):
        rows=candles([10,11,12,9,8,8])
        event=type("Event",(),{"kind":"BREAK_UP","level":10,"candle_index":1,"close":11})()
        result=outcome_stats(rows,[event],3); self.assertEqual(result["n"],1); self.assertEqual(result["fail_pct"],100.0)

    def test_prefix_causality_returns_deterministic_checks(self):
        rows=candles([10,12,9,11,13,10,12,14,11,13,15,12,14,16,13,15,17,14,16,18,15])
        results=prefix_causality(rows,window=1); self.assertTrue(results); self.assertTrue(all(isinstance(ok,bool) for _,ok in results))

    def test_mtf_closed_prefix_excludes_unclosed_and_future_bars(self):
        rows=candles([10,11,12,13],step=3600)
        self.assertEqual([r["timestamp"] for r in closed_prefix(rows,3600,7200)],[0,3600])

if __name__=="__main__": unittest.main()
