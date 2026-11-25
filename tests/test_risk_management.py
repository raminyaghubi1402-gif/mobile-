import unittest

from lili_trader.risk_management import (
    fee_adjusted_breakeven_stop,
    propose_structural_trailing_stop,
)


class FeeAdjustedBreakevenTests(unittest.TestCase):
    def test_covers_both_entry_and_exit_fees_and_exit_slippage(self):
        stop = fee_adjusted_breakeven_stop(
            101.0, fee_rate=0.001, slippage_rate=0.001
        )
        net = (stop * (1 - 0.001) - 101.0) - (
            stop * (1 - 0.001) + 101.0
        ) * 0.001
        self.assertAlmostEqual(net, 0.0, places=10)

    def test_rejects_invalid_costs_and_prices(self):
        for kwargs in (
            {"entry_fill_price": 0, "fee_rate": 0.001, "slippage_rate": 0.001},
            {"entry_fill_price": 100, "fee_rate": 1, "slippage_rate": 0.001},
            {"entry_fill_price": 100, "fee_rate": 0.001, "slippage_rate": -0.1},
        ):
            with self.assertRaises(ValueError):
                fee_adjusted_breakeven_stop(**kwargs)


class StructuralTrailingStopTests(unittest.TestCase):
    @staticmethod
    def candle(ts, open_, high, low, close):
        return {
            "timestamp": ts, "fetched_at": ts + 300,
            "open": open_, "high": high, "low": low, "close": close,
        }

    def setUp(self):
        # A confirmed bullish sequence with a higher low after the entry.
        prices = [
            (100, 103, 99, 102), (102, 105, 101, 104),
            (104, 106, 102, 103), (103, 104, 100, 101),
            (101, 103, 99, 102), (102, 106, 101, 105),
            (105, 108, 104, 107), (107, 109, 105, 106),
            (106, 108, 103, 104), (104, 107, 102, 106),
            (106, 110, 105, 109), (109, 112, 109, 111),
            (111, 113, 110, 112), (112, 114, 108, 111),
            (111, 115, 110, 114), (114, 117, 112, 116),
            (116, 118, 113, 115), (115, 117, 112, 116),
            (116, 120, 115, 119), (119, 122, 117, 121),
        ]
        self.candles = [
            self.candle(1_800_000_000 + i * 300, *row)
            for i, row in enumerate(prices)
        ]
        self.entry_timestamp = self.candles[5]["timestamp"]

    def call(self, candles=None, **overrides):
        args = {
            "entry_timestamp": self.entry_timestamp,
            "entry_fill_price": 105.0,
            "current_stop": 99.0,
            "fee_rate": 0.001,
            "slippage_rate": 0.001,
            "pivot_window": 2,
            "atr_period": 5,
            "atr_buffer_fraction": 0.1,
        }
        args.update(overrides)
        return propose_structural_trailing_stop(
            self.candles if candles is None else candles, **args
        )

    def test_raises_stop_only_to_confirmed_higher_low_above_net_breakeven(self):
        result = self.call()
        self.assertIsNotNone(result)
        self.assertGreater(result.stop_loss, result.breakeven_floor)
        self.assertGreater(result.stop_loss, 99.0)
        self.assertLess(result.stop_loss, self.candles[-1]["low"])

    def test_never_loosens_existing_stop(self):
        result = self.call(current_stop=200.0)
        self.assertIsNone(result)

    def test_no_lookahead_before_right_side_confirmation(self):
        result = self.call(candles=self.candles[:9])
        self.assertIsNone(result)

    def test_missing_entry_or_timestamp_gap_fails_closed(self):
        self.assertIsNone(self.call(candles=self.candles[6:]))
        broken = list(self.candles)
        broken[10] = dict(broken[10], timestamp=broken[10]["timestamp"] + 300)
        self.assertIsNone(self.call(candles=broken))

    def test_candidate_already_traded_through_is_rejected(self):
        broken = list(self.candles)
        broken[-1] = dict(broken[-1], low=90.0)
        self.assertIsNone(self.call(candles=broken))

    def test_unclosed_or_unverified_candles_fail_closed(self):
        unclosed = list(self.candles)
        unclosed[-1] = dict(unclosed[-1], fetched_at=unclosed[-1]["timestamp"] + 299)
        self.assertIsNone(self.call(candles=unclosed))
        unverified = list(self.candles)
        del unverified[-1]["fetched_at"]
        self.assertIsNone(self.call(candles=unverified))

    def test_no_trailing_when_not_above_fee_adjusted_breakeven(self):
        self.assertIsNone(self.call(entry_fill_price=150.0))

    def test_invalid_configuration_is_rejected(self):
        with self.assertRaises(ValueError):
            self.call(atr_buffer_fraction=0)
        with self.assertRaises(ValueError):
            self.call(pivot_window=0)


if __tests__;
