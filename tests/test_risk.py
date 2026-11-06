import unittest

from lili_trader.risk import RiskLimits, RiskSnapshot, evaluate_order


class RiskGateTests(unittest.TestCase):
    def setUp(self):
        self.limits = RiskLimits(
            max_order_quote=100,
            max_position_quote=250,
            max_daily_loss_quote=20,
            max_drawdown_pct=10,
            max_daily_trades=5,
            max_market_data_age_seconds=60,
        )

    def test_all_checks_pass(self):
        result = evaluate_order(50, self.limits, RiskSnapshot())
        self.assertTrue(result.allowed)
        self.assertEqual(result.reasons, ())

    def test_order_and_position_caps(self):
        state = RiskSnapshot(open_position_quote=210)
        result = evaluate_order(50, self.limits, state)
        self.assertFalse(result.allowed)
        self.assertTrue(any("سقف کل موقعیت" in reason for reason in result.reasons))

    def test_daily_loss_and_drawdown_stop(self):
        state = RiskSnapshot(daily_loss_quote=20, drawdown_pct=10)
        result = evaluate_order(10, self.limits, state)
        self.assertFalse(result.allowed)
        self.assertEqual(len(result.reasons), 2)

    def test_stale_data_and_unconfirmed_exchange_fail_closed(self):
        state = RiskSnapshot(market_data_age_seconds=61, exchange_state_confirmed=False)
        result = evaluate_order(10, self.limits, state)
        self.assertFalse(result.allowed)
        self.assertEqual(len(result.reasons), 2)

    def test_kill_switch_and_cooldown(self):
        state = RiskSnapshot(kill_switch_active=True, cooldown_active=True)
        result = evaluate_order(10, self.limits, state)
        self.assertFalse(result.allowed)
        self.assertEqual(len(result.reasons), 2)

    def test_invalid_order_rejected(self):
        result = evaluate_order(0, self.limits, RiskSnapshot())
        self.assertFalse(result.allowed)

    def test_nan_fails_closed(self):
        result = evaluate_order(float("nan"), self.limits, RiskSnapshot())
        self.assertFalse(result.allowed)
        self.assertIn("دادهٔ عددی نامعتبر است؛ توقف امن.", result.reasons)

    def test_invalid_limits_rejected(self):
        limits = RiskLimits(0, 100, 10, 10, 3)
        with self.assertRaises(ValueError):
            evaluate_order(1, limits, RiskSnapshot())


if __name__ == "__main__":
    unittest.main()
