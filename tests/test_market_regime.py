import unittest
from types import SimpleNamespace
from lili_trader.market_regime import apply_primary_trend, classify_market_regime, select_strategy_candidates
from lili_trader.strategy_engines import STRATEGY_CATALOG

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

    def test_uptrend_selects_independent_catalogued_engines(self):
        trend=SimpleNamespace(direction="up",last_two_swing_highs=(110,120),last_two_swing_lows=(100,110))
        state=classify_market_regime(candles(),trend)
        names=[candidate.name for candidate in select_strategy_candidates(state)]
        expected=set(STRATEGY_CATALOG) - {"range_trading"}
        self.assertEqual(set(names), expected)
        self.assertEqual(len(names), len(expected))

    def test_downtrend_is_wait_in_spot_only_but_selects_engines_when_short_allowed(self):
        rows=[]
        for i in range(30):
            close=130-i
            rows.append({"timestamp":i,"open":close+0.5,"high":close+1,
                         "low":close-1,"close":close-0.5})
        trend=SimpleNamespace(direction="down",last_two_swing_highs=(120,110),last_two_swing_lows=(110,100))
        state=classify_market_regime(rows,trend)
        spot_names=[candidate.name for candidate in select_strategy_candidates(state)]
        self.assertEqual(spot_names, ["no_trade_wait"])
        short_names=[candidate.name for candidate in select_strategy_candidates(state, allow_short=True)]
        self.assertEqual(set(short_names), set(STRATEGY_CATALOG) - {"range_trading"})

    def test_selector_rejects_non_boolean_short_permission(self):
        trend=SimpleNamespace(direction="up",last_two_swing_highs=(110,120),last_two_swing_lows=(100,110))
        state=classify_market_regime(candles(),trend)
        with self.assertRaises(ValueError):
            select_strategy_candidates(state, allow_short=1)

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


    def test_boolean_ohlc_values_are_rejected(self):
        trend = SimpleNamespace(direction="up", last_two_swing_highs=(110, 120),
                                last_two_swing_lows=(100, 110))
        for field in ("open", "high", "low", "close"):
            with self.subTest(field=field):
                rows = candles()
                rows[-1][field] = True
                with self.assertRaises(ValueError):
                    classify_market_regime(rows, trend)

    def test_non_positive_ohlc_values_are_rejected(self):
        trend = SimpleNamespace(direction="up", last_two_swing_highs=(110, 120),
                                last_two_swing_lows=(100, 110))
        for field in ("open", "high", "low", "close"):
            with self.subTest(field=field):
                rows = candles()
                rows[-1][field] = 0
                with self.assertRaises(ValueError):
                    classify_market_regime(rows, trend)

    def test_malformed_volatility_window_is_rejected(self):
        trend = SimpleNamespace(direction="up", last_two_swing_highs=(110, 120),
                                last_two_swing_lows=(100, 110))
        for value in (True, 20.0, "20", 0):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    classify_market_regime(candles(), trend, volatility_window=value)


    def test_one_hour_range_uses_fifteen_minute_direction(self):
        base = classify_market_regime(
            candles(),
            SimpleNamespace(direction="up", last_two_swing_highs=(110,120),
                            last_two_swing_lows=(100,110)),
        )
        up = apply_primary_trend(base, "range", "up")
        self.assertEqual(up.trend, "up")
        self.assertIn("15m", up.reason)
        self.assertEqual(
            set(candidate.name for candidate in select_strategy_candidates(up)),
            set(STRATEGY_CATALOG) - {"range_trading"},
        )
        down = apply_primary_trend(base, "range", "down")
        self.assertEqual(down.trend, "down")
        self.assertEqual(
            [candidate.name for candidate in select_strategy_candidates(down)],
            ["no_trade_wait"],
        )

    def test_directional_one_hour_remains_primary_over_fifteen_minute(self):
        base = classify_market_regime(
            candles(),
            SimpleNamespace(direction="up", last_two_swing_highs=(110,120),
                            last_two_swing_lows=(100,110)),
        )
        result = apply_primary_trend(base, "up", "down")
        self.assertEqual(result.trend, "up")
        self.assertIn("1h", result.reason)

    def test_unknown_one_hour_does_not_silently_fall_back_to_fifteen_minute(self):
        base = classify_market_regime(
            candles(),
            SimpleNamespace(direction="up", last_two_swing_highs=(110,120),
                            last_two_swing_lows=(100,110)),
        )
        result = apply_primary_trend(base, "unknown", "up")
        self.assertEqual(result.trend, "unknown")
        self.assertEqual(
            [candidate.name for candidate in select_strategy_candidates(result)],
            ["no_trade_wait"],
        )

if __name__ == "__main__":
    unittest.main()
