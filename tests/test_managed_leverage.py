import unittest

from lili_trader.leverage_paper import open_isolated_position
from lili_trader.managed_leverage import (
    ManagedPositionError,
    open_managed_leverage_paper_position,
    update_managed_leverage_paper_position,
)


CALM = [
    {"high": 101, "low": 99, "close": 100},
    {"high": 102, "low": 100, "close": 101},
    {"high": 103, "low": 101, "close": 102},
    {"high": 104, "low": 102, "close": 103},
]


class ManagedLeverageTests(unittest.TestCase):
    def test_long_position_attaches_stop_and_closes_on_reversal(self):
        position = open_isolated_position("long", 100, 2, 100)
        state = open_managed_leverage_paper_position(
            position, CALM, risk_budget_quote=20, atr_period=2,
            stop_atr_multiple=2,
        )
        self.assertLess(state.stop.initial_stop, 100)
        moved = update_managed_leverage_paper_position(state, 110)
        self.assertEqual(moved.event, "open")
        stopped = update_managed_leverage_paper_position(moved.state, 105)
        self.assertEqual(stopped.event, "stopped")
        self.assertEqual(stopped.state.status, "closed")
        self.assertEqual(stopped.state.close_reason, "smart_stop")
        self.assertIsNotNone(stopped.state.net_pnl_after_fees)

    def test_short_position_attaches_stop_and_closes(self):
        position = open_isolated_position("short", 100, 2, 100)
        state = open_managed_leverage_paper_position(
            position, CALM, risk_budget_quote=20, atr_period=2,
            stop_atr_multiple=2,
        )
        self.assertGreater(state.stop.initial_stop, 100)
        moved = update_managed_leverage_paper_position(state, 90)
        self.assertEqual(moved.event, "open")
        stopped = update_managed_leverage_paper_position(moved.state, 95)
        self.assertEqual(stopped.event, "stopped")

    def test_rejects_position_over_risk_budget(self):
        position = open_isolated_position("long", 100, 3, 100)
        with self.assertRaises(ManagedPositionError):
            open_managed_leverage_paper_position(
                position, CALM, risk_budget_quote=0.01, atr_period=2,
            )

    def test_rejects_stop_too_close_to_liquidation(self):
        position = open_isolated_position("long", 100, 3, 100)
        extreme = [
            {"high": 110, "low": 90, "close": 100},
            {"high": 112, "low": 88, "close": 100},
        ]
        with self.assertRaises(ManagedPositionError):
            open_managed_leverage_paper_position(
                position, extreme, risk_budget_quote=100,
                atr_period=2, max_stop_distance_pct=50,
            )

    def test_liquidation_precedes_stop(self):
        position = open_isolated_position("long", 100, 3, 100)
        state = open_managed_leverage_paper_position(
            position, CALM, risk_budget_quote=100, atr_period=2,
            liquidation_buffer_pct=1,
        )
        result = update_managed_leverage_paper_position(
            state, position.liquidation_price * 0.99,
        )
        self.assertEqual(result.event, "liquidated")
        self.assertEqual(result.state.status, "liquidated")

    def test_closed_position_is_idempotent(self):
        position = open_isolated_position("long", 100, 2, 100)
        state = open_managed_leverage_paper_position(
            position, CALM, risk_budget_quote=20, atr_period=2,
        )
        moved = update_managed_leverage_paper_position(state, 110)
        stopped = update_managed_leverage_paper_position(moved.state, 105)
        again = update_managed_leverage_paper_position(stopped.state, 1)
        self.assertEqual(again.event, "already_closed")
        self.assertEqual(again.state.exit_fee, stopped.state.exit_fee)


if __name__ == "__main__":
    unittest.main()
