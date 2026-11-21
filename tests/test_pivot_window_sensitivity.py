import unittest
from scripts.pivot_window_sensitivity import event_key, causality_checks
from lili_trader.market_structure import analyze_market_structure

def sample():
    highs=[10,12,11,15,13,14,16,14,18,16,17,19,17,20,18,21,19,22,20,23,21]
    lows=[7,8,9,8,10,9,11,10,12,11,13,12,14,13,15,14,16,15,17,16,18]
    rows=[]
    for i,(h,l) in enumerate(zip(highs,lows)):
        o=(h+l)/2
        rows.append({"timestamp":i*300,"open":o,"high":h,"low":l,"close":o,"volume":1})
    return rows
class PivotWindowSensitivityTests(unittest.TestCase):
    def test_event_key_rounds_price_and_is_stable(self):
        e=type("E",(),{"kind":"BOS_UP","candle_index":7,"level":12.123456789})()
        self.assertEqual(event_key(e),("BOS_UP",7,12.12345679))
    def test_prefix_causality_returns_checks(self):
        checks=causality_checks(sample(),1)
        self.assertTrue(checks); self.assertTrue(all(type(ok) is bool for _,ok in checks))
    def test_analyzer_does_not_mutate_rows(self):
        rows=sample(); before=[r.copy() for r in rows]
        analyze_market_structure(rows,pivot_window=1); analyze_market_structure(rows,pivot_window=3)
        self.assertEqual(rows,before)
if __name__=="__main__": unittest.main()
