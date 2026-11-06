import unittest
from dataclasses import dataclass
from math import sin

from lili_trader.price_action_backtest import run_three_timeframe_backtest


@dataclass
class Bar:
    timestamp: int
    open: float
    high: float
    low: float
    close: float


def rising_bars(count, seconds, start):
    bars = []
    for i in range(count):
        center = 100 + i * 0.18 + 2.5 * sin(i * 0.55)
        previous = 100 + max(0, i - 1) * 0.18 + 2.5 * sin(max(0, i - 1) * 0.55)
        o, close = previous, center
        bars.append(Bar(start + i * seconds, o, max(o, close) + 0.35,
                        min(o, close) - 0.35, close))
    return bars


def falling_bars(count, seconds, start):
    bars = []
    for i in range(count):
        center = 300 - i * 0.18 - 2.5 * sin(i * 0.55)
        previous = 300 - max(0, i - 1) * 0.18 - 2.5 * sin(max(0, i - 1) * 0.55)
        o, close = previous, center
        bars.append(Bar(start + i * seconds, o, max(o, close) + 0.35,
                        min(o, close) - 0.35, close))
    return bars


class ThreeTimeframeBacktestTests(unittest.TestCase):
    def test_runs_deterministically_and_never_uses_leverage(self):
        hourly = rising_bars(80, 3600, 0)
        fifteen = rising_bars(220, 900, 0)
        five = rising_bars(650, 300, 0)
        kwargs = dict(starting_cash=100_000, fee_rate=0.001, slippage_rate=0.001)
        first = run_three_timeframe_backtest(hourly, fifteen, five, **kwargs)
        second = run_three_timeframe_backtest(hourly, fifteen, five, **kwargs)
        self.assertEqual(first, second)
        self.assertLessEqual(first.buy_count + first.sell_count, first.trade_count)
        self.assertLessEqual(first.trade_count, len(five))
        self.assertGreaterEqual(first.ending_equity, 0)

    def test_downtrend_can_only_create_sell_positions(self):
        hourly = falling_bars(80, 3600, 0)
        fifteen = falling_bars(220, 900, 0)
        five = falling_bars(650, 300, 0)
        result = run_three_timeframe_backtest(hourly, fifteen, five, starting_cash=100_000)
        self.assertEqual(result.buy_count, 0)
        self.assertEqual(result.trade_count, result.sell_count)

    def test_rejects_missing_or_irregular_market_data(self):
        hourly = rising_bars(40, 3600, 0)
        fifteen = rising_bars(80, 900, 0)
        five = rising_bars(100, 300, 0)
        with self.assertRaises(ValueError):
            run_three_timeframe_backtest(hourly[:9], fifteen, five)
        five[30].timestamp += 300
        with self.assertRaises(ValueError):
            run_three_timeframe_backtest(hourly, fifteen, five)

    def test_rejects_risk_above_two_percent(self):
        with self.assertRaises(ValueError):
            run_three_timeframe_backtest(
                rising_bars(40, 3600, 0), rising_bars(80, 900, 0),
                rising_bars(100, 300, 0), risk_fraction=0.03,
            )

    def test_no_trades_when_hourly_history_is_only_range(self):
        # Flat, alternating candles create no consistently rising/falling structure.
        hourly = []
        for i in range(50):
            p = 100 + (1 if i % 2 else -1)
            hourly.append(Bar(i * 3600, p, p + 1, p - 1, p))
        fifteen = rising_bars(120, 900, 0)
        five = rising_bars(250, 300, 0)
        result = run_three_timeframe_backtest(hourly, fifteen, five, starting_cash=100_000)
        self.assertEqual(result.trade_count, 0)


if __name__ == "__main__":
    unittest.main()
