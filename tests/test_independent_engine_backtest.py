import unittest
from types import SimpleNamespace
from unittest.mock import patch

from lili_trader.independent_engine_backtest import run_independent_engine_backtest
from lili_trader.nobitex_data import NobitexCandle
from lili_trader.strategy_engines import StrategyDecision
from lili_trader.risk_management import StopUpdate


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

    def test_signal_enters_at_the_immediately_following_candle_open(self):
        start = 1_800_000_000

        def flat(resolution, count, step):
            return [
                NobitexCandle(
                    symbol="BTCIRT", resolution=resolution,
                    timestamp=start + i * step,
                    open=100.0, high=101.0, low=99.0, close=100.0,
                    volume=10.0, fetched_at=start + i * step + step,
                )
                for i in range(count)
            ]

        hourly = flat("60", 60, 3600)
        fifteen = flat("15", 240, 900)
        five = flat("5", 720, 300)

        class OneSignalRegistry:
            registered_ids = ("breakout",)

            def evaluate_selected(self, snapshot, selected):
                return (StrategyDecision(
                    strategy_id="breakout",
                    status="SIGNAL",
                    reason="test-only deterministic setup",
                    evidence={
                        "entry_reference": 100.0,
                        "stop_loss": 98.0,
                        "take_profit": 103.25,
                    },
                ),)

        with (
            patch("lili_trader.independent_engine_backtest.build_default_strategy_registry",
                  return_value=OneSignalRegistry()),
            patch("lili_trader.independent_engine_backtest.build_independent_engine_snapshots",
                  return_value={"breakout": {}}),
            patch("lili_trader.independent_engine_backtest.classify_price_action_trend",
                  return_value=object()),
            patch("lili_trader.independent_engine_backtest.classify_market_regime",
                  return_value=SimpleNamespace(trend="up", volatility="normal")),
            patch("lili_trader.independent_engine_backtest.select_strategy_candidates",
                  return_value=(SimpleNamespace(name="breakout", eligible=True),)),
        ):
            result = run_independent_engine_backtest(
                hourly, fifteen, five, minimum_order_quote=1.0
            )

        breakout = next(row for row in result.engines if row.strategy_id == "breakout")
        self.assertEqual(breakout.trade_count, 1)
        self.assertEqual(breakout.entry_attempts, 1)
        self.assertEqual(breakout.cancelled_rr, 0)
        self.assertEqual(breakout.trades[0].entry_timestamp, five[360].timestamp)
        self.assertAlmostEqual(breakout.trades[0].entry_price, 100.1)

    def test_confirmed_trailing_stop_applies_only_to_the_next_candle(self):
        start = 1_800_000_000

        def flat(resolution, count, step):
            return [
                NobitexCandle(
                    symbol="BTCIRT", resolution=resolution,
                    timestamp=start + i * step,
                    open=100.0, high=101.0, low=99.0, close=100.0,
                    volume=10.0, fetched_at=start + i * step + step,
                )
                for i in range(count)
            ]

        hourly = flat("60", 60, 3600)
        fifteen = flat("15", 240, 900)
        five = flat("5", 720, 300)
        row = five[362]
        five[362] = NobitexCandle(
            symbol="BTCIRT", resolution="5", timestamp=row.timestamp,
            open=99.0, high=100.5, low=98.4, close=100.0,
            volume=10.0, fetched_at=row.timestamp + 300,
        )

        class OneSignalRegistry:
            registered_ids = ("breakout",)

            def __init__(self):
                self.sent = False

            def evaluate_selected(self, snapshot, selected):
                if self.sent:
                    return (StrategyDecision("breakout", "WAIT", "already signaled"),)
                self.sent = True
                return (StrategyDecision(
                    "breakout", "SIGNAL", "test-only deterministic setup",
                    {"entry_reference": 100.0, "stop_loss": 98.0, "take_profit": 103.25},
                ),)

        def trailing_update(candles, **kwargs):
            if len(candles) >= 2:
                return StopUpdate(
                    stop_loss=98.5, swing_low=99.0,
                    swing_timestamp=candles[-2].timestamp,
                    breakeven_floor=98.2, atr=1.0,
                )
            return None

        with (
            patch("lili_trader.independent_engine_backtest.build_default_strategy_registry",
                  return_value=OneSignalRegistry()),
            patch("lili_trader.independent_engine_backtest.build_independent_engine_snapshots",
                  return_value={"breakout": {}}),
            patch("lili_trader.independent_engine_backtest.classify_price_action_trend",
                  return_value=object()),
            patch("lili_trader.independent_engine_backtest.classify_market_regime",
                  return_value=SimpleNamespace(trend="up", volatility="normal")),
            patch("lili_trader.independent_engine_backtest.select_strategy_candidates",
                  return_value=(SimpleNamespace(name="breakout", eligible=True),)),
            patch("lili_trader.independent_engine_backtest.propose_structural_trailing_stop",
                  side_effect=trailing_update),
        ):
            result = run_independent_engine_backtest(
                hourly, fifteen, five, minimum_order_quote=1.0
            )

        breakout = next(row for row in result.engines if row.strategy_id == "breakout")
        self.assertEqual(breakout.trade_count, 1)
        self.assertEqual(breakout.trades[0].exit_reason, "smart_stop")
        self.assertEqual(breakout.trades[0].exit_timestamp, five[362].timestamp + 300)

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

    def test_rejects_invalid_risk_and_minimum_order_parameters(self):
        with self.assertRaises(ValueError):
            run_independent_engine_backtest(
                self.hourly, self.fifteen, self.five, risk_fraction=0.1
            )
        with self.assertRaises(ValueError):
            run_independent_engine_backtest(
                self.hourly, self.fifteen, self.five, minimum_order_quote=0
            )


if __name__ == "__main__":
    unittest.main()
