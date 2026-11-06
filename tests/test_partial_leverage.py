import unittest

from lili_trader.leverage_paper import open_isolated_position
from lili_trader.managed_leverage import open_managed_leverage_paper_position
from lili_trader.partial_leverage import (
    PartialExitError, TakeProfitStage, start_partial_exit_plan, update_partial_exit,
)

CANDLES = [
    {"high": 101, "low": 99, "close": 100},
    {"high": 102, "low": 100, "close": 101},
    {"high": 103, "low": 101, "close": 102},
    {"high": 104, "low": 102, "close": 103},
]


def managed(side="long"):
    p = open_isolated_position(side, 100, 2, 100)
    return open_managed_leverage_paper_position(
        p, CANDLES, risk_budget_quote=20, atr_period=2, stop_atr_multiple=2,
    )


class PartialLeverageTests(unittest.TestCase):
    def test_long_two_stages_close_half_and_leave_runner(self):
        state = start_partial_exit_plan(managed("long"))
        first = update_partial_exit(state, 104)
        self.assertEqual(first.event, "partial_take_profit")
        self.assertEqual(len(first.fills), 1)
        self.assertAlmostEqual(first.state.remaining_quantity, state.remaining_quantity * 0.75)
        second = update_partial_exit(first.state, 108)
        self.assertEqual(second.event, "partial_take_profit")
        self.assertAlmostEqual(second.state.remaining_quantity, state.remaining_quantity * 0.5)
        self.assertEqual(second.state.completed_stages, (0, 1))
        self.assertGreater(second.state.realized_gross_pnl, 0)

    def test_short_targets_are_below_entry(self):
        state = start_partial_exit_plan(managed("short"))
        first = update_partial_exit(state, 96)
        self.assertEqual(first.event, "partial_take_profit")
        self.assertLess(first.fills[0][0], 100)

    def test_gap_uses_target_not_better_market_price(self):
        state = start_partial_exit_plan(managed("long"))
        result = update_partial_exit(state, 120)
        self.assertEqual(result.fills[0][0], 104)
        self.assertLess(result.fills[0][0], 120)

    def test_no_duplicate_partial_fills(self):
        state = start_partial_exit_plan(managed("long"))
        first = update_partial_exit(state, 104)
        again = update_partial_exit(first.state, 104)
        self.assertEqual(again.event, "open")
        self.assertEqual(again.fills, ())
        self.assertEqual(again.state.completed_stages, (0,))

    def test_liquidation_precedes_take_profit(self):
        state = start_partial_exit_plan(managed("long"))
        p = state.original_position
        result = update_partial_exit(state, p.liquidation_price * 0.99)
        self.assertEqual(result.event, "liquidation")
        self.assertEqual(result.state.status, "liquidated")

    def test_rejects_invalid_stage_configuration(self):
        with self.assertRaises(PartialExitError):
            start_partial_exit_plan(managed(), stages=())
        with self.assertRaises(PartialExitError):
            start_partial_exit_plan(
                managed(), stages=(
                    TakeProfitStage(1, .6),
                    TakeProfitStage(2, .5),
                ),
            )

    def test_closed_state_is_idempotent(self):
        state = start_partial_exit_plan(managed("long"))
        first = update_partial_exit(state, 104)
        second = update_partial_exit(first.state, 120)
        stopped = update_partial_exit(second.state, 100)
        again = update_partial_exit(stopped.state, 1)
        self.assertEqual(again.event, "already_closed")


if __name__ == "__main__":
    unittest.main()
