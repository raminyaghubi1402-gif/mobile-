import unittest

from lili_trader.strategy_engines import build_default_strategy_registry


class DefaultRegistryTests(unittest.TestCase):
    def test_only_implemented_engines_are_registered(self):
        registry = build_default_strategy_registry()
        self.assertEqual(set(registry.registered_ids), {"trend_pullback", "breakout", "breakout_retest", "support_resistance_rejection", "volume_trading", "ict"})

    def test_other_catalog_engines_remain_disabled(self):
        registry = build_default_strategy_registry()
        decisions = registry.evaluate_selected(
            {}, ("range_trading",)
        )
        self.assertEqual([d.status for d in decisions], ["DISABLED"])


if __name__ == "__main__":
    unittest.main()
