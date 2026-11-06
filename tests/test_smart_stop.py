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


if __name__ == "__main__":
    unittest.main()
