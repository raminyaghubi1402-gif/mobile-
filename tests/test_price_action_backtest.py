import unittest
from dataclasses import dataclass
from math import sin, isfinite
from types import SimpleNamespace
from unittest.mock import patch

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

    def test_open_position_equity_mark_uses_remaining_quantity(self):
        trend = SimpleNamespace(
            direction="up",
            last_two_swing_highs=(110.0, 115.0),
            last_two_swing_lows=(90.0, 95.0),
        )
        with patch("lili_trader.price_action_backtest.classify_price_action_trend",
                   return_value=trend):
            result = run_three_timeframe_backtest(
                rising_bars(80, 3600, 0),
                rising_bars(220, 900, 0),
                rising_bars(650, 300, 0),
                starting_cash=100_000,
            )
        self.assertGreater(result.trade_count, 0)
        self.assertTrue(all(isfinite(value) for value in result.equity_curve))
    def test_long_and_short_staged_exits_account_for_both_partials_and_runner(self):
        def flat_bars(count, seconds):
            return [Bar(i * seconds, 100.0, 101.0, 99.0, 100.0)
                    for i in range(count)]

        for direction in ("up", "down"):
            if direction == "up":
                trend = SimpleNamespace(direction="up",
                    last_two_swing_highs=(110.0, 115.0),
                    last_two_swing_lows=(90.0, 95.0))
            else:
                trend = SimpleNamespace(direction="down",
                    last_two_swing_highs=(110.0, 105.0),
                    last_two_swing_lows=(90.0, 85.0))
            five = flat_bars(121, 300)
            if direction == "up":
                five[-1] = Bar(120 * 300, 100.0, 109.0, 99.5, 100.0)
            else:
                five[-1] = Bar(120 * 300, 100.0, 100.5, 91.0, 100.0)
            with patch("lili_trader.price_action_backtest.classify_price_action_trend",
                       return_value=trend):
                result = run_three_timeframe_backtest(
                    flat_bars(80, 3600), flat_bars(220, 900), five,
                    starting_cash=100_000, fee_rate=0.001, slippage_rate=0.0)
            self.assertEqual(result.trade_count, 1)
            trade = result.trades[0]
            self.assertEqual(trade.direction, "BUY" if direction == "up" else "SELL")
            self.assertAlmostEqual(trade.quantity, 125.0)
            self.assertAlmostEqual(trade.gross_pnl, 375.0)
            self.assertAlmostEqual(trade.exit_price, 103.0 if direction == "up" else 97.0)
            expected_fees = 25.375 if direction == "up" else 24.625
            self.assertAlmostEqual(trade.fees, expected_fees)
            self.assertAlmostEqual(trade.net_pnl, 375.0 - expected_fees)
    def test_break_even_stop_after_first_partial_activates_next_candle(self):
        def flat_bars(count, seconds):
            return [Bar(i * seconds, 100.0, 101.0, 99.0, 100.0)
                    for i in range(count)]

        trend = SimpleNamespace(direction="up",
            last_two_swing_highs=(110.0, 115.0),
            last_two_swing_lows=(90.0, 95.0))
        five = flat_bars(122, 300)
        five[120] = Bar(120 * 300, 100.0, 104.5, 99.5, 104.0)
        five[121] = Bar(121 * 300, 100.0, 100.5, 99.5, 100.0)
        with patch("lili_trader.price_action_backtest.classify_price_action_trend",
                   return_value=trend):
            result = run_three_timeframe_backtest(
                flat_bars(80, 3600), flat_bars(220, 900), five,
                starting_cash=100_000, fee_rate=0.001, slippage_rate=0.0)
        self.assertEqual(result.trade_count, 1)
        trade = result.trades[0]
        self.assertEqual(trade.exit_reason, "smart_stop")
        self.assertAlmostEqual(trade.gross_pnl, 125.0)
        self.assertAlmostEqual(trade.exit_price, 101.0)
        self.assertAlmostEqual(trade.fees, 25.125)
        self.assertAlmostEqual(trade.net_pnl, 99.875)
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
