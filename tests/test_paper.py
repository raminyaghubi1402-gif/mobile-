import unittest

from lili_trader.paper import PaperAccount, PaperTradingError


class PaperAccountTests(unittest.TestCase):
    def setUp(self):
        self.account = PaperAccount(1000, fee_rate=0.01)

    def test_buy_charges_fee_and_updates_equity(self):
        fill = self.account.execute("b1", "buy", 2, 100)
        self.assertAlmostEqual(fill.fee_quote, 2)
        self.assertAlmostEqual(self.account.cash, 798)
        self.assertEqual(self.account.position, 2)
        self.assertAlmostEqual(self.account.equity(100), 998)

    def test_sell_only_existing_position(self):
        self.account.execute("b1", "buy", 2, 100)
        fill = self.account.execute("s1", "sell", 1, 120)
        self.assertAlmostEqual(fill.cash_after, 916.8)
        self.assertEqual(fill.position_after, 1)

    def test_idempotent_order_id_does_not_double_fill(self):
        first = self.account.execute("same", "buy", 1, 100)
        second = self.account.execute("same", "buy", 1, 100)
        self.assertEqual(first, second)
        self.assertEqual(len(self.account.fills), 1)
        self.assertEqual(self.account.position, 1)

    def test_rejects_insufficient_cash_and_oversell_without_mutation(self):
        with self.assertRaises(PaperTradingError):
            self.account.execute("too-big", "buy", 100, 100)
        with self.assertRaises(PaperTradingError):
            self.account.execute("sell", "sell", 1, 100)
        self.assertEqual(self.account.cash, 1000)
        self.assertEqual(self.account.position, 0)
        self.assertEqual(self.account.fills, ())

    def test_invalid_numbers_and_side_rejected(self):
        for q, p in ((0, 10), (1, float("nan")), (1, float("inf"))):
            with self.assertRaises(PaperTradingError):
                self.account.execute("bad"+str(q)+str(p), "buy", q, p)
        with self.assertRaises(PaperTradingError):
            self.account.execute("bad-side", "short", 1, 10)

    def test_equity_rejects_bad_mark(self):
        with self.assertRaises(PaperTradingError):
            self.account.equity(0)


if __name__ == "__main__":
    unittest.main()
