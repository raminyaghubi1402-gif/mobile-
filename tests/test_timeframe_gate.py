import unittest
from lili_trader.price_action_trend import TrendState
from lili_trader.timeframe_gate import evaluate_timeframe_gate


def state(direction, highs=(120.0, 130.0), lows=(90.0, 100.0)):
    return TrendState(direction, direction, 100.0, highs, lows, 3, 3, "test")


class TimeframeGateTests(unittest.TestCase):
    def setUp(self):
        self.up = state("up")
        self.down = state("down", highs=(110.0, 100.0), lows=(90.0, 80.0))
        self.range = state("range")
        self.unknown = state("unknown")

    def test_only_aligned_three_timeframes_are_ready(self):
        result = evaluate_timeframe_gate(self.up, self.up, self.up, 105)
        self.assertTrue(result.allowed)
        self.assertEqual(result.direction, "up")

    def test_hourly_range_or_unknown_blocks(self):
        for hourly in (self.range, self.unknown):
            self.assertFalse(evaluate_timeframe_gate(hourly, self.up, self.up, 105).allowed)

    def test_opposite_15m_is_a_pullback_not_a_short_signal(self):
        result = evaluate_timeframe_gate(self.up, self.down, self.down, 105)
        self.assertEqual(result.status, "pullback")
        self.assertFalse(result.allowed)
        self.assertEqual(result.direction, "up")

    def test_15m_break_of_hourly_swing_invalidates_setup(self):
        result = evaluate_timeframe_gate(self.up, self.down, self.up, 99)
        self.assertEqual(result.status, "invalidated")
        self.assertFalse(result.allowed)

    def test_5m_must_confirm_same_direction(self):
        result = evaluate_timeframe_gate(self.up, self.up, self.down, 105)
        self.assertEqual(result.status, "wait_trigger")
        self.assertFalse(result.allowed)

    def test_missing_15m_close_fails_closed(self):
        result = evaluate_timeframe_gate(self.up, self.up, self.up, None)
        self.assertFalse(result.allowed)


if __name__ == "__main__":
    unittest.main()
