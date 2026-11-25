import unittest

from lili_trader.independent_engine_backtest import run_independent_engine_backtest
from lili_trader.nobitex_data import NobitexCandle


def series(symbol, resolution, count, start, step, price_start=100.0):
    rows = []
    for i in range(count):
        ts = start + i * step
        price = price_start + i * 0.1
        rows.append(NobitexCandle(
            symbol=symbol, resolution=resolution, timestamp=ts,
            open=price, high=price + 1, low=price - 1, close=price + 0.2,
            volume=10.0, fetched_at=ts + step,
        ))
    return rows


class IndependentEngineBacktestTests(unittest.TestCase):
    def setUp(self):
        start = 1_800_000_000
        self.hourly = series("BTCIRT", "60", 60, start, 3600)
        self.fifteen = series("BTCIRT", "15", 240, start, 900)
        self.five = series("BTCIRT", "5", 720, start, 300)

    def test_runs_all_engines_in_isolated_ledgers(self):
        result = run_independent_engine_backtest(
            self.hourly, self.fifteen, self.five
        )
        self.assertEqual(result.symbol, "BTCIRT")
        self.assertEqual(result.bars, 720)
        self.assertTrue(all(row.evaluated_bars > 0 for row in result.engines))
        self.assertEqual(
            {row.strategy_id for row in result.engines},
            {"trend_pullback", "breakout", "breakout_retest",
             "support_resistance_rejection", "volume_trading", "ict"},
        )
        self.assertTrue(all(row.starting_cash == 13_550_000 for row in result.engines))
        self.assertTrue(all(row.ending_equity >= 0 for row in result.engines))

    def test_rejects_gapped_or_mixed_symbol_history(self):
        broken = list(self.five)
        broken[100] = NobitexCandle(
            symbol="BTCIRT", resolution="5",
            timestamp=broken[100].timestamp + 300,
            open=100, high=101, low=99, close=100, volume=1,
            fetched_at=broken[100].timestamp + 600,
        )
        with self.assertRaises(ValueError):
            run_independent_engine_backtest(self.hourly, self.fifteen, broken)
        wrong_symbol = [row.__class__(
            symbol="ETHIRT", resolution=row.resolution, timestamp=row.timestamp,
            open=row.open, high=row.high, low=row.low, close=row.close,
            volume=row.volume, fetched_at=row.fetched_at,
        ) for row in self.five]
        with self.assertRaises(ValueError):
            run_independent_engine_backtest(self.hourly, self.fifteen, wrong_symbol)

    def test_rejects_invalid_risk_parameters(self):
        with self.assertRaises(ValueError):
            run_independent_engine_backtest(
                self.hourly, self.fifteen, self.five, risk_fraction=0.1
            )


if __name__ == "__main__":
    unittest.main()
