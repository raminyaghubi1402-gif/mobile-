import unittest
from scripts.trend_pullback_historical_diagnostic import closed_prefix, freshness, snapshot_at, signal_outcome

def candles(times,step):
    return [{"timestamp":t,"open":10.0,"high":11.0,"low":9.0,"close":10.0,"volume":1.0} for t in times]

class TrendPullbackHistoricalDiagnosticTests(unittest.TestCase):
    def test_closed_prefix_excludes_higher_tf_future_candles(self):
        rows=candles([0,3600,7200],3600)
        self.assertEqual([r["timestamp"] for r in closed_prefix(rows,3600,7200)],[0,3600])
    def test_freshness_fails_stale_and_recent_gap(self):
        self.assertTrue(freshness(candles([0,300],300),300,600))
        self.assertFalse(freshness(candles([0,300],300),300,1200))
        self.assertFalse(freshness(candles([0,900],300),300,1200))
    def test_snapshot_only_contains_closed_candles(self):
        h1=candles([0,3600,7200],3600)
        m15=candles([0,900,1800,2700,3600,4500,5400],900)
        m5=candles(list(range(0,6000,300)),300)
        snapshot=snapshot_at(h1,m15,m5,12)
        self.assertIsNotNone(snapshot)
        as_of=snapshot["as_of_timestamp"]
        self.assertTrue(all(r["timestamp"]+3600<=as_of for r in snapshot["candles"]["1h"]))
        self.assertTrue(all(r["timestamp"]+900<=as_of for r in snapshot["candles"]["15m"]))
        self.assertTrue(all(r["timestamp"]+300<=as_of for r in snapshot["candles"]["5m"]))
    def test_outcome_excludes_forward_gap(self):
        rows=candles(list(range(0,4500,300)),300)
        rows[7]["timestamp"]+=300
        self.assertIsNone(signal_outcome(rows,4,"BUY",10.0,3))
if __name__=="__main__":unittest.main()
