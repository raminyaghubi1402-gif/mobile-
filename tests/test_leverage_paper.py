import unittest

from lili_trader.leverage_paper import (
    LeverageError, open_isolated_position, mark_position,
)


class LeveragePaperTests(unittest.TestCase):
    def test_long_and_short_pnl(self):
        long = open_isolated_position("long", 100, 2, 1000)
        self.assertAlmostEqual(long.quantity, 0.2)
        self.assertGreater(mark_position(long, 1100).unrealized_pnl, 0)
        self.assertLess(mark_position(long, 900).unrealized_pnl, 0)
        short = open_isolated_position("short", 100, 2, 1000)
        self.assertGreater(mark_position(short, 900).unrealized_pnl, 0)
        self.assertLess(mark_position(short, 1100).unrealized_pnl, 0)

    def test_leverage_cap_and_bad_inputs(self):
        with self.assertRaises(LeverageError):
            open_isolated_position("long", 100, 10, 1000)
        with self.assertRaises(LeverageError):
            open_isolated_position("long", 100, float("nan"), 1000)
        with self.assertRaises(LeverageError):
            open_isolated_position("sideways", 100, 2, 1000)

    def test_liquidation_flag(self):
        p = open_isolated_position("long", 100, 3, 1000)
        self.assertTrue(mark_position(p, p.liquidation_price * 0.99).liquidated)
        q = open_isolated_position("short", 100, 3, 1000)
        self.assertTrue(mark_position(q, q.liquidation_price * 1.01).liquidated)

    def test_mark_rejects_invalid_price(self):
        p = open_isolated_position("long", 100, 2, 1000)
        with self.assertRaises(LeverageError):
            mark_position(p, 0)


if __name__ == "__main__":
    unittest.main()
