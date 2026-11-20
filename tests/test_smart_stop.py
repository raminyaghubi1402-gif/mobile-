import unittest

from lili_trader.smart_stop import (
    SmartStopError, create_smart_stop, update_smart_stop,
)


class SmartStopTests(unittest.TestCase):
    def test_long_stop_locks_profit_in_stages(self):
        state = create_smart_stop("long", 100, 90, 2)
        first = update_smart_stop(state, 110)
        self.assertEqual(first.state.stage_index, 0)
        self.assertGreaterEqual(first.state.current_stop, 100)
        second = update_smart_stop(first.state, 120)
        self.assertEqual(second.state.stage_index, 1)
        self.assertGreaterEqual(second.state.current_stop, 110)
        third = update_smart_stop(second.state, 130)
        self.assertEqual(third.state.stage_index, 2)
        self.assertGreaterEqual(third.state.current_stop, 120)

    def test_short_stop_locks_profit_in_stages(self):
        state = create_smart_stop("short", 100, 110, 2)
        first = update_smart_stop(state, 90)
        self.assertEqual(first.state.stage_index, 0)
        self.assertLessEqual(first.state.current_stop, 100)
        second = update_smart_stop(first.state, 80)
        self.assertEqual(second.state.stage_index, 1)
        self.assertLessEqual(second.state.current_stop, 90)

    def test_long_stop_never_moves_back_after_reversal(self):
        state = create_smart_stop("long", 100, 90, 2)
        advanced = update_smart_stop(state, 125).state
        before = advanced.current_stop
        reversed_market = update_smart_stop(advanced, 105)
        self.assertGreaterEqual(reversed_market.state.current_stop, before)

    def test_short_stop_never_moves_back_after_reversal(self):
        state = create_smart_stop("short", 100, 110, 2)
        advanced = update_smart_stop(state, 75).state
        before = advanced.current_stop
        reversed_market = update_smart_stop(advanced, 95)
        self.assertLessEqual(reversed_market.state.current_stop, before)

    def test_invalid_prices_and_wrong_initial_stop_rejected(self):
        with self.assertRaises(SmartStopError):
            create_smart_stop("long", 100, 110, 2)
        with self.assertRaises(SmartStopError):
            create_smart_stop("short", 100, 90, 2)
        state = create_smart_stop("long", 100, 90, 2)
        with self.assertRaises(SmartStopError):
            update_smart_stop(state, float("nan"))

    def test_bad_atr_multiplier_rejected(self):
        state = create_smart_stop("long", 100, 90, 2)
        with self.assertRaises(SmartStopError):
            update_smart_stop(state, 105, atr_trailing_multiple=0)

    def test_adaptive_atr_widens_trail_when_volatility_expands(self):
        state = create_smart_stop("long", 100, 90, 2)
        normal = update_smart_stop(state, 120, current_atr=2)
        expanded = update_smart_stop(state, 120, current_atr=4)
        # ATR widening provides more breathing room, while stop remains above
        # the initial stop and never violates monotonicity.
        self.assertGreaterEqual(expanded.state.current_stop, state.current_stop)
        self.assertLessEqual(expanded.state.current_stop, normal.state.current_stop)

    def test_structure_trailing_candidate_for_long(self):
        state = create_smart_stop("long", 100, 90, 2)
        update = update_smart_stop(state, 115, structure_level=108,
                                  structure_buffer_atr=0.5)
        self.assertGreaterEqual(update.state.current_stop, 107)

    def test_structure_trailing_candidate_for_short(self):
        state = create_smart_stop("short", 100, 110, 2)
        update = update_smart_stop(state, 85, structure_level=90,
                                  structure_buffer_atr=0.5)
        self.assertLessEqual(update.state.current_stop, 91)

    def test_structure_level_wrong_side_rejected(self):
        state = create_smart_stop("long", 100, 90, 2)
        with self.assertRaises(SmartStopError):
            update_smart_stop(state, 105, structure_level=106)

    def test_stop_never_gives_back_locked_profit_when_atr_expands(self):
        state = create_smart_stop("long", 100, 90, 2)
        tight = update_smart_stop(state, 130, current_atr=1).state
        wider_atr = update_smart_stop(tight, 128, current_atr=8)
        self.assertGreaterEqual(wider_atr.state.current_stop, tight.current_stop)


if __name__ == "__main__":
    unittest.main()
