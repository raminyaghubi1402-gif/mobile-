import unittest
from scripts.close_displacement_audit import displacement_atr, eligible_events, segment_metrics

def candles(n=40,step=300):
    rows=[]
    for i in range(n):
        close=100+i*0.1
        rows.append({"timestamp":i*step,"open":close,"high":close+0.5,"low":close-0.5,"close":close,"volume":1})
    return rows

class CloseDisplacementAuditTests(unittest.TestCase):
    def test_up_event_displacement_is_positive_and_atr_normalized(self):
        rows=candles()
        e=type("Event",(),{"kind":"BOS_UP","candle_index":20,"level":102,"close":102.5})()
        self.assertGreater(displacement_atr(rows,e,300),0)
    def test_down_event_uses_inverted_direction(self):
        rows=candles()
        e=type("Event",(),{"kind":"CHOCH_DOWN","candle_index":20,"level":102,"close":101.5})()
        self.assertGreater(displacement_atr(rows,e,300),0)
    def test_atr_rejects_gap_in_lookback(self):
        rows=candles(); rows[19]["timestamp"]+=300
        e=type("Event",(),{"kind":"BOS_UP","candle_index":20,"level":102,"close":102.5})()
        self.assertIsNone(displacement_atr(rows,e,300))
    def test_future_horizon_requires_contiguous_candles(self):
        rows=candles(); rows[22]["timestamp"]+=300
        e=type("Event",(),{"kind":"BOS_UP","level":102,"candle_index":20,"close":102.5})()
        result=segment_metrics(rows,[e],300,6,0,40)
        self.assertEqual(result["n"],0)
        self.assertEqual(result["skipped"],1)
if __name__=="__main__": unittest.main()
