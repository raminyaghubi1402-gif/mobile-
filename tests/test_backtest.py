import unittest
from dataclasses import dataclass

from lili_trader.backtest import run_sma_backtest


@dataclass
class Bar:
    timestamp: int
    open: float
    high: float
    low: float
    close: float


def sample_bars():
    closes = [10, 10, 10, 9, 8, 7, 8, 9, 10, 12, 14, 15, 14, 13]
    return [
        Bar(i, value, value * 1.01, value * 0.99, value)
        for i, value in enumerate(closes)
    ]


class BacktestTests(unittest.TestCase):
    def test_enters_on_bar_after_confirmed_crossover(self):
        result = run_sma_backtest(
            sample_bars(), fast_period=2, slow_period=3,
            fee_rate=0.001, slippage_rate=0.001,
        )
        self.assertGreaterEqual(result.trade_count, 1)
        # The crossover is known at close of candle 7; entry is candle 8 open.
        self.assertEqual(result.trades[0].entry_timestamp, 8)

    def test_fees_and_slippage_reduce_trade_result(self):
        bars = sample_bars()
        charged = run_sma_backtest(
            bars, fast_period=2, slow_period=3,
            fee_rate=0.01, slippage_rate=0.01,
        )
        free = run_sma_backtest(
            bars, fast_period=2, slow_period=3,
            fee_rate=0.0, slippage_rate=0.000001,
        )
        self.assertGreater(charged.trades[0].fees, 0)
        self.assertLess(charged.trades[0].net_pnl, free.trades[0].net_pnl)

    def test_stop_is_assumed_first_when_stop_and_target_both_touch(self):
        bars = sample_bars()
        bars[9] = Bar(9, 10, 11, 9, 10)
        result = run_sma_backtest(
            bars, fast_period=2, slow_period=3,
            stop_loss_fraction=0.02, take_profit_fraction=0.04,
            fee_rate=0.0, slippage_rate=0.0,
        )
        self.assertEqual(result.trades[0].exit_reason, "stop_loss")

    def test_stop_gap_fills_at_worse_open_price(self):
        bars = sample_bars()
        # Entry is around 10; stop is around 9.8. A gap below stop
        # must not be optimistically filled at the stop price.
        bars[9] = Bar(9, 9.5, 11, 9.0, 10)
        result = run_sma_backtest(
            bars, fast_period=2, slow_period=3,
            stop_loss_fraction=0.02, take_profit_fraction=0.04,
            fee_rate=0.0, slippage_rate=0.0,
        )
        self.assertEqual(result.trades[0].exit_reason, "stop_loss")
        self.assertAlmostEqual(result.trades[0].exit_price, 9.5)

    def test_deterministic_results(self):
        first = run_sma_backtest(sample_bars(), fast_period=2, slow_period=3)
        second = run_sma_backtest(sample_bars(), fast_period=2, slow_period=3)
        self.assertEqual(first, second)

    def test_rejects_bad_or_unsorted_candles(self):
        bars = sample_bars()
        bars[2] = Bar(1, 10, 11, 9, 10)
        with self.assertRaises(ValueError):
            run_sma_backtest(bars, fast_period=2, slow_period=3)

        bars = sample_bars()
        bars[2] = Bar(2, 10, 9, 11, 10)
        with self.assertRaises(ValueError):
            run_sma_backtest(bars, fast_period=2, slow_period=3)

    def test_rejects_invalid_cost_parameters(self):
        with self.assertRaises(ValueError):
            run_sma_backtest(sample_bars(), fee_rate=float("nan"))


if __name__ == "__main__":
    unittest.main()
