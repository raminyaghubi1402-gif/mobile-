import unittest
from types import SimpleNamespace
from lili_trader.market_regime import classify_market_regime, select_strategy_candidates

def candles(count=30, step=1.0, width=2.0):
    rows=[]
    for i in range(count):
        close=100+i*step
        rows.append({"timestamp":i,"open":close-width/2,"high":close+width/2,
                     "low":close-width/2,"close":close})
    return rows

class MarketRegimeTests(unittest.TestCase):
    def test_insufficient_data_fails_closed(self):
        trend=SimpleNamespace(direction="up",last_two_swing_highs=(2,3),last_two_swing_lows=(1,2))
        state=classify_market_regime(candles(5),trend)
        self.assertEqual(state.trend,"unknown")
        self.assertEqual(state.allowed_strategies,())

    def test_uptrend_selects_only_long_candidates(self):
        trend=SimpleNamespace(direction="up",last_two_swing_highs=(110,120),last_two_swing_lows=(100,110))
        state=classify_market_regime(candles(),trend)
        names=[c.name for c in select_strategy_candidates(state)]
        self.assertTrue(names)
        self.assertTrue(all("buy" in name for name in names))

    def test_downtrend_selects_only_short_candidates(self):
        rows=[]
        for i in range(30):
            close=130-i
            rows.append({"timestamp":i,"open":close+0.5,"high":close+1,
                         "low":close-1,"close":close-0.5})
        trend=SimpleNamespace(direction="down",last_two_swing_highs=(120,110),last_two_swing_lows=(110,100))
        state=classify_market_regime(rows,trend)
        names=[c.name for c in select_strategy_candidates(state)]
        self.assertTrue(names)
        self.assertTrue(all(("sell" in name) or name=="no_trade_wait" for name in names))

    def test_range_policy_is_no_trade(self):
        trend=SimpleNamespace(direction="range",last_two_swing_highs=(110,112),last_two_swing_lows=(100,98))
        state=classify_market_regime(candles(),trend)
        names=[c.name for c in select_strategy_candidates(state)]
        self.assertEqual(names,["no_trade_range_policy"])

    def test_high_volatility_forces_wait(self):
        rows=candles()
        for i in range(25,30):
            close=rows[i]["close"]
            rows[i].update(open=close-20,high=close+30,low=close-30,close=close)
        trend=SimpleNamespace(direction="up",last_two_swing_highs=(110,120),last_two_swing_lows=(100,110))
        state=classify_market_regime(rows,trend)
        names=[c.name for c in select_strategy_candidates(state)]
        self.assertEqual(names,["no_trade_wait"])

if __name__ == "__main__":
    unittest.main()
