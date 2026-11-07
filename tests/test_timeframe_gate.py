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

    def ready_up(self, fifteen=None):
        return evaluate_timeframe_gate(
            self.up, fifteen or self.down, self.up, 105,
            setup_zone_touched=True, previous_5m_close=129,
            latest_5m_close=131,
        )

    def test_hourly_up_allows_15m_countertrend_pullback_only_with_zone_and_5m_break(self):
        result = self.ready_up()
        self.assertTrue(result.allowed)
        self.assertEqual(result.direction, "up")
        self.assertEqual(result.status, "ready")
        self.assertIn("اصلاح", result.reason)

    def test_hourly_range_or_unknown_blocks(self):
        for hourly in (self.range, self.unknown):
            result = evaluate_timeframe_gate(
                hourly, self.up, self.up, 105, setup_zone_touched=True,
                previous_5m_close=129, latest_5m_close=131,
            )
            self.assertFalse(result.allowed)

    def test_no_zone_means_wait_even_when_timeframes_align(self):
        result = evaluate_timeframe_gate(
            self.up, self.up, self.up, 105, setup_zone_touched=False,
            previous_5m_close=129, latest_5m_close=131,
        )
        self.assertEqual(result.status, "wait_zone")
        self.assertFalse(result.allowed)

    def test_15m_pullback_with_no_5m_reversal_waits(self):
        result = evaluate_timeframe_gate(
            self.up, self.down, self.down, 105, setup_zone_touched=True,
            previous_5m_close=131, latest_5m_close=129,
        )
        self.assertEqual(result.status, "wait_trigger")
        self.assertFalse(result.allowed)

    def test_5m_must_close_through_last_confirmed_swing_high(self):
        result = evaluate_timeframe_gate(
            self.up, self.up, self.up, 105, setup_zone_touched=True,
            previous_5m_close=130, latest_5m_close=130,
        )
        self.assertEqual(result.status, "wait_trigger")
        self.assertFalse(result.allowed)

    def test_downtrend_pullback_requires_bearish_5m_break(self):
        result = evaluate_timeframe_gate(
            self.down, self.up, self.down, 85, setup_zone_touched=True,
            previous_5m_close=91, latest_5m_close=79,
        )
        self.assertTrue(result.allowed)
        self.assertEqual(result.direction, "down")

    def test_fresh_independent_5m_bos_on_latest_bar_allows_entry(self):
        from types import SimpleNamespace
        event = SimpleNamespace(kind="BOS_UP", candle_index=24)
        structure = SimpleNamespace(bias="up", last_event=event)
        result = evaluate_timeframe_gate(
            self.up, self.down, self.up, 105, setup_zone_touched=True,
            previous_5m_close=129, latest_5m_close=131,
            five_minute_structure=structure, five_minute_candle_count=25,
        )
        self.assertTrue(result.allowed)
        self.assertEqual(result.status, "ready")

    def test_missing_5m_structure_blocks_when_strict_confirmation_is_required(self):
        result = evaluate_timeframe_gate(
            self.up, self.down, self.up, 105, setup_zone_touched=True,
            previous_5m_close=129, latest_5m_close=131,
            five_minute_structure=None, five_minute_candle_count=25,
            require_structure_confirmation=True,
        )
        self.assertEqual(result.status, "wait_structure")
        self.assertFalse(result.allowed)

    def test_boolean_or_invalid_candle_count_cannot_authorize_fresh_structure(self):
        from types import SimpleNamespace
        structure = SimpleNamespace(
            bias="up", last_event=SimpleNamespace(kind="BOS_UP", candle_index=0)
        )
        for count in (True, 0, -1, 25.0, "25", None):
            with self.subTest(count=count):
                result = evaluate_timeframe_gate(
                    self.up, self.down, self.up, 105, setup_zone_touched=True,
                    previous_5m_close=129, latest_5m_close=131,
                    five_minute_structure=structure,
                    five_minute_candle_count=count,
                )
                self.assertEqual(result.status, "wait_structure")
                self.assertFalse(result.allowed)

    def test_boolean_or_fractional_structure_event_index_is_rejected(self):
        from types import SimpleNamespace
        for index in (True, 24.5, "24", -1):
            with self.subTest(index=index):
                structure = SimpleNamespace(
                    bias="up", last_event=SimpleNamespace(kind="BOS_UP", candle_index=index)
                )
                result = evaluate_timeframe_gate(
                    self.up, self.down, self.up, 105, setup_zone_touched=True,
                    previous_5m_close=129, latest_5m_close=131,
                    five_minute_structure=structure, five_minute_candle_count=25,
                )
                self.assertEqual(result.status, "wait_structure")
                self.assertFalse(result.allowed)

    def test_stale_or_wrong_direction_5m_structure_event_blocks_entry(self):
        from types import SimpleNamespace
        cases = (
            SimpleNamespace(bias="up", last_event=SimpleNamespace(kind="BOS_UP", candle_index=23)),
            SimpleNamespace(bias="down", last_event=SimpleNamespace(kind="BOS_UP", candle_index=24)),
            SimpleNamespace(bias="up", last_event=SimpleNamespace(kind="CHOCH_DOWN", candle_index=24)),
        )
        for structure in cases:
            with self.subTest(structure=structure):
                result = evaluate_timeframe_gate(
                    self.up, self.down, self.up, 105, setup_zone_touched=True,
                    previous_5m_close=129, latest_5m_close=131,
                    five_minute_structure=structure, five_minute_candle_count=25,
                )
                self.assertEqual(result.status, "wait_structure")
                self.assertFalse(result.allowed)

    def test_15m_break_of_hourly_swing_invalidates_setup(self):
        result = evaluate_timeframe_gate(
            self.up, self.down, self.up, 99, setup_zone_touched=True,
            previous_5m_close=129, latest_5m_close=131,
        )
        self.assertEqual(result.status, "invalidated")
        self.assertFalse(result.allowed)

    def test_missing_15m_close_fails_closed(self):
        for invalid in (None, float("nan"), float("inf"), 0, "not-a-price"):
            result = evaluate_timeframe_gate(
                self.up, self.up, self.up, invalid, setup_zone_touched=True,
                previous_5m_close=129, latest_5m_close=131,
            )
            self.assertFalse(result.allowed)

    def test_missing_hourly_structural_lows_fails_closed(self):
        malformed = state("up", lows=None)
        result = evaluate_timeframe_gate(
            malformed, self.up, self.up, 105, setup_zone_touched=True,
            previous_5m_close=129, latest_5m_close=131,
        )
        self.assertEqual(result.status, "blocked")
        self.assertFalse(result.allowed)

    def test_missing_hourly_structural_highs_fails_closed(self):
        malformed = state("down", highs=None, lows=(90.0, 80.0))
        result = evaluate_timeframe_gate(
            malformed, self.down, self.down, 85, setup_zone_touched=True,
            previous_5m_close=91, latest_5m_close=79,
        )
        self.assertEqual(result.status, "blocked")
        self.assertFalse(result.allowed)

    def test_non_numeric_hourly_pivots_fail_closed_without_crashing(self):
        malformed_up = state("up", lows=(90.0, "bad"))
        malformed_down = state("down", highs=(110.0, None))
        for hourly, close in ((malformed_up, 105), (malformed_down, 85)):
            result = evaluate_timeframe_gate(
                hourly, self.up, self.up, close, setup_zone_touched=True,
                previous_5m_close=129, latest_5m_close=131,
            )
            self.assertEqual(result.status, "blocked")
            self.assertFalse(result.allowed)

    def test_boolean_pivot_values_cannot_authorize_a_setup(self):
        malformed = state("up", lows=(90.0, True))
        result = evaluate_timeframe_gate(
            malformed, self.up, self.up, 105, setup_zone_touched=True,
            previous_5m_close=129, latest_5m_close=131,
        )
        self.assertEqual(result.status, "blocked")
        self.assertFalse(result.allowed)

    def test_boolean_prices_cannot_authorize_a_setup(self):
        for close_15m, previous_5m, latest_5m in (
            (True, 129, 131), (105, True, 131), (105, 129, True)
        ):
            with self.subTest(values=(close_15m, previous_5m, latest_5m)):
                result = evaluate_timeframe_gate(
                    self.up, self.down, self.up, close_15m,
                    setup_zone_touched=True, previous_5m_close=previous_5m,
                    latest_5m_close=latest_5m,
                )
                self.assertFalse(result.allowed)


if __name__ == "__main__":
    unittest.main()
