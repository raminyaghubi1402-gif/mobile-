import unittest
from types import SimpleNamespace
from unittest.mock import patch

from lili_trader.strategy_engines import create_research_registry
from lili_trader.trend_pullback_engine import TrendPullbackEngine


def make_snapshot(as_of=36000):
    candles = {}
    for timeframe, interval, count in (("1h", 3600, 9), ("15m", 900, 9), ("5m", 300, 9)):
        start = as_of - interval * count
        candles[timeframe] = [
            {"timestamp": start + i * interval, "open": 100.0, "high": 101.0,
             "low": 99.0, "close": 100.0, "is_closed": True}
            for i in range(count)
        ]
    return {"as_of_timestamp": as_of, "candles": candles}


def event(kind, index, timestamp):
    return SimpleNamespace(kind=kind, candle_index=index, timestamp=timestamp)


def analysis(bias, last_event):
    return SimpleNamespace(bias=bias, last_event=last_event)


def uptrend():
    return SimpleNamespace(direction="up", last_two_swing_highs=(110, 120),
                          last_two_swing_lows=(90, 95), reason="HH/HL")


class TrendPullbackEngineTests(unittest.TestCase):
    def setUp(self):
        self.engine = TrendPullbackEngine()
        self.snapshot = make_snapshot()

    def evaluate_with_structures(self, m15, m5, trend=None):
        with patch("lili_trader.trend_pullback_engine.classify_price_action_trend",
                   return_value=trend or uptrend()), patch(
            "lili_trader.trend_pullback_engine.analyze_market_structure",
            side_effect=(m15, m5),
        ):
            return self.engine.evaluate(self.snapshot)

    def valid_structures(self):
        m15_event = event("CHOCH_UP", 7, 36000 - 1800)
        m5_event = event("BOS_UP", 8, 36000 - 300)
        return analysis("up", m15_event), analysis("up", m5_event)

    def test_valid_setup_returns_research_signal_only(self):
        m15, m5 = self.valid_structures()
        result = self.evaluate_with_structures(m15, m5)
        self.assertEqual(result.status, "SIGNAL")
        self.assertEqual(result.evidence["direction"], "BUY")
        self.assertIsNone(result.evidence["entry_order"])
        self.assertIsNone(result.evidence["stop_loss"])
        self.assertIsNone(result.evidence["take_profit"])

    def test_m15_choch_must_be_recent_and_aligned(self):
        m15, m5 = self.valid_structures()
        m15.last_event = event("CHOCH_UP", 5, 36000 - 3600)
        result = self.evaluate_with_structures(m15, m5)
        self.assertEqual(result.status, "WAIT")

    def test_m5_bos_must_be_fresh_and_after_m15_close(self):
        m15, m5 = self.valid_structures()
        m5.last_event = event("BOS_UP", 7, 36000 - 600)
        result = self.evaluate_with_structures(m15, m5)
        self.assertEqual(result.status, "WAIT")

    def test_range_h1_fails_closed_without_structure_analysis(self):
        with patch("lili_trader.trend_pullback_engine.classify_price_action_trend",
                   return_value=SimpleNamespace(direction="range", reason="mixed")) as trend, patch(
            "lili_trader.trend_pullback_engine.analyze_market_structure"
        ) as structure:
            result = self.engine.evaluate(self.snapshot)
        self.assertEqual(result.status, "NO_SETUP")
        structure.assert_not_called()

    def test_h1_protected_level_break_invalidates_setup(self):
        m15, m5 = self.valid_structures()
        self.snapshot["candles"]["15m"][-1]["close"] = 94.0
        result = self.evaluate_with_structures(m15, m5)
        self.assertEqual(result.status, "INVALIDATED")

    def test_open_candle_is_rejected(self):
        self.snapshot["candles"]["5m"][-1]["is_closed"] = False
        result = self.engine.evaluate(self.snapshot)
        self.assertEqual(result.status, "WAIT")
        self.assertEqual(result.evidence["data_validation"], "failed")

    def test_future_candle_is_rejected(self):
        self.snapshot["candles"]["15m"][-1]["timestamp"] = 36000
        result = self.engine.evaluate(self.snapshot)
        self.assertEqual(result.status, "WAIT")
        self.assertEqual(result.evidence["data_validation"], "failed")

    def test_stale_candle_is_rejected(self):
        self.snapshot["candles"]["5m"][-1]["timestamp"] -= 600
        result = self.engine.evaluate(self.snapshot)
        self.assertEqual(result.status, "WAIT")
        self.assertEqual(result.evidence["data_validation"], "failed")

    def test_unaligned_candle_timestamp_is_rejected(self):
        self.snapshot["candles"]["5m"][-1]["timestamp"] -= 1
        result = self.engine.evaluate(self.snapshot)
        self.assertEqual(result.status, "WAIT")
        self.assertEqual(result.evidence["data_validation"], "failed")

    def test_research_registry_factory_registers_only_first_engine(self):
        registry = create_research_registry()
        self.assertEqual(registry.registered_ids, ("trend_pullback",))


if __name__ == "__main__":
    unittest.main()
