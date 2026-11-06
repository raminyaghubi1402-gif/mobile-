import unittest

from lili_trader.volatility_stop import VolatilityStopError, build_volatility_stop
from lili_trader.smart_stop import update_smart_stop


CANDLES = [
    {"high": 102, "low": 98, "close": 100},
    {"high": 103, "low": 99, "close": 102},
    {"high": 104, "low": 100, "close": 103},
]


class VolatilityStopTests(unittest.TestCase):
    def test_long_stop_distance_and_size_follow_atr(self):
        plan = build_volatility_stop(
            "long", 103, CANDLES, atr_period=2,
            stop_atr_multiple=2, risk_budget_quote=20,
            max_position_quote=500,
        )
        self.assertLess(plan.state.initial_stop, 103)
        self.assertAlmostEqual(
            plan.stop_distance, 2 * plan.atr_value
        )
        self.assertLessEqual(plan.risk_quote, 20)
        self.assertLessEqual(plan.notional_quote, 500)

    def test_short_stop_is_above_entry(self):
        plan = build_volatility_stop(
            "short", 103, CANDLES, atr_period=2,
            stop_atr_multiple=2, risk_budget_quote=20,
            max_position_quote=500,
        )
        self.assertGreater(plan.state.initial_stop, 103)

    def test_higher_volatility_reduces_position_for_same_risk_budget(self):
        quiet = [
            {"high": 100.5, "low": 99.5, "close": 100},
            {"high": 100.6, "low": 99.6, "close": 100},
        ]
        volatile = [
            {"high": 110, "low": 90, "close": 100},
            {"high": 112, "low": 88, "close": 100},
        ]
        quiet_plan = build_volatility_stop(
            "long", 100, quiet, atr_period=2, risk_budget_quote=10,
            max_position_quote=1000,
        )
        volatile_plan = build_volatility_stop(
            "long", 100, volatile, atr_period=2, risk_budget_quote=10,
            max_position_quote=1000, max_stop_distance_pct=50,
        )
        self.assertLess(volatile_plan.quantity, quiet_plan.quantity)

    def test_extreme_volatility_blocks_new_entry(self):
        extreme = [
            {"high": 120, "low": 80, "close": 100},
            {"high": 125, "low": 75, "close": 100},
        ]
        with self.assertRaises(VolatilityStopError):
            build_volatility_stop(
                "long", 100, extreme, atr_period=2,
                risk_budget_quote=10, max_position_quote=1000,
            )

    def test_stop_manager_freezes_entry_atr_after_creation(self):
        plan = build_volatility_stop(
            "long", 103, CANDLES, atr_period=2,
            risk_budget_quote=20, max_position_quote=500,
        )
        first = update_smart_stop(plan.state, 110)
        # A subsequent ATR reading is not an argument to the stop manager;
        # its frozen entry ATR is retained and the stop cannot retreat.
        second = update_smart_stop(first.state, 106)
        self.assertEqual(second.state.initial_atr, plan.atr_value)
        self.assertGreaterEqual(second.state.current_stop, first.state.current_stop)

    def test_rejects_invalid_budget_and_direction(self):
        with self.assertRaises(VolatilityStopError):
            build_volatility_stop(
                "long", 103, CANDLES, atr_period=2,
                risk_budget_quote=0, max_position_quote=500,
            )
        with self.assertRaises(VolatilityStopError):
            build_volatility_stop(
                "sideways", 103, CANDLES, atr_period=2,
                risk_budget_quote=10, max_position_quote=500,
            )


if __name__ == "__main__":
    unittest.main()
