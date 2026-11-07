import unittest
from decimal import Decimal

from lili_trader.order_constraints import MarketOrderRules, validate_order_size


class OrderConstraintTests(unittest.TestCase):
    def setUp(self):
        self.rules = MarketOrderRules(
            min_quantity=Decimal("0.01"),
            quantity_step=Decimal("0.01"),
            min_notional=Decimal("10"),
        )

    def test_quantity_rounds_down_to_step_not_up(self):
        result = validate_order_size("0.019", "1000", self.rules)
        self.assertTrue(result.accepted)
        self.assertEqual(result.quantity, Decimal("0.01"))
        self.assertEqual(result.notional, Decimal("10"))

    def test_accepts_exact_minimum_quantity_and_notional(self):
        result = validate_order_size("0.01", "1000", self.rules)
        self.assertTrue(result.accepted)
        self.assertEqual(result.notional, Decimal("10"))

    def test_unknown_rules_fail_closed(self):
        result = validate_order_size("1", "100", None)
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "market_rules_unknown")

    def test_rejects_below_minimum_quantity(self):
        result = validate_order_size("0.009", "2000", self.rules)
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "below_minimum_quantity")

    def test_rejects_below_minimum_notional(self):
        result = validate_order_size("0.01", "999", self.rules)
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "below_minimum_notional")

    def test_rule_values_are_normalized_from_config_strings_and_floats(self):
        rules = MarketOrderRules("0.01", 0.01, "10")
        self.assertEqual(rules.min_quantity, Decimal("0.01"))
        self.assertEqual(rules.quantity_step, Decimal("0.01"))
        self.assertEqual(rules.min_notional, Decimal("10"))
        self.assertIsInstance(rules.quantity_step, Decimal)

    def test_invalid_rule_types_fail_with_value_error(self):
        with self.assertRaises(ValueError):
            MarketOrderRules(None, Decimal("0.1"), Decimal("1"))

    def test_invalid_rules_and_nonfinite_values_rejected(self):
        with self.assertRaises(ValueError):
            MarketOrderRules(Decimal("0"), Decimal("0.1"), Decimal("1"))
        with self.assertRaises(ValueError):
            validate_order_size("NaN", "10", self.rules)


if __name__ == "__main__":
    unittest.main()
