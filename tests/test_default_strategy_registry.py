import unittest

from lili_trader.strategy_engines import build_default_strategy_registry


class DefaultRegistryTests(unittest.TestCase):
    def test_only_implemented_engines_are_registered(self):
        registry = build_default_strategy_registry()
        self.assertEqual(set(registry.registered_ids), {"trend_pullback", "breakout", "breakout_retest", "support_resistance_rejection", "volume_trading"})

    def test_other_catalog_engines_remain_disabled(self):
        registry = build_default_strategy_registry()
        decisions = registry.evaluate_selected(
            {}, ("range_trading", "ict")
        )
        self.assertEqual([d.status for d in decisions], ["DISABLED"] * 2)


if __name__ == "__main__":
    unittest.main()
