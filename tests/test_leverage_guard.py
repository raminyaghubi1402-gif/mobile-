import unittest
from lili_trader.leverage_guard import open_risk_checked_paper_position
from lili_trader.risk import RiskLimits, RiskSnapshot

class LeverageGuardTests(unittest.TestCase):
    def setUp(self):
        self.limits = RiskLimits(300, 500, 20, 10, 5, 60)

    def test_allowed_position_checks_full_notional(self):
        result = open_risk_checked_paper_position("long", 100, 2, 1000, self.limits, RiskSnapshot())
        self.assertTrue(result.allowed)
        self.assertIsNotNone(result.position)
        self.assertAlmostEqual(result.position.quantity, 0.2)

    def test_order_cap_blocks_based_on_notional(self):
        limits = RiskLimits(150, 500, 20, 10, 5, 60)
        result = open_risk_checked_paper_position("long", 100, 2, 1000, limits, RiskSnapshot())
        self.assertFalse(result.allowed)
        self.assertIsNone(result.position)

    def test_kill_switch_and_stale_data_block(self):
        for state in (RiskSnapshot(kill_switch_active=True), RiskSnapshot(market_data_age_seconds=61)):
            result = open_risk_checked_paper_position("short", 50, 2, 1000, self.limits, state)
            self.assertFalse(result.allowed)
            self.assertIsNone(result.position)

    def test_invalid_inputs_fail_closed(self):
        result = open_risk_checked_paper_position("long", 100, float("nan"), 1000, self.limits, RiskSnapshot())
        self.assertFalse(result.allowed)
        self.assertIsNone(result.position)

if __name__ == "__main__":
    unittest.main()
