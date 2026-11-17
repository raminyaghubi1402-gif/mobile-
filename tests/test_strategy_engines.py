import unittest

from lili_trader.strategy_engines import (
    STRATEGY_CATALOG,
    StrategyDecision,
    StrategyRegistry,
)


class IndependentEngine:
    strategy_id = "volume_trading"
    enabled = True

    def __init__(self):
        self.calls = 0

    def evaluate(self, market_snapshot):
        self.calls += 1
        # Deliberately ignore the legacy three-timeframe confirmation field.
        return StrategyDecision(
            strategy_id=self.strategy_id,
            status="SIGNAL",
            reason="volume engine's own test rule passed",
            evidence={"volume_rule_passed": True},
        )


class StrategyEngineRegistryTests(unittest.TestCase):
    def test_catalog_contains_exactly_the_seven_agreed_engines(self):
        self.assertEqual(len(STRATEGY_CATALOG), 7)
        self.assertEqual(len(set(STRATEGY_CATALOG)), 7)

    def test_unimplemented_engines_are_disabled_not_guessed(self):
        decisions = StrategyRegistry().evaluate_selected(
            {}, ("trend_pullback", "ict")
        )
        self.assertEqual([item.status for item in decisions], ["DISABLED", "DISABLED"])

    def test_volume_engine_is_not_blocked_by_legacy_three_timeframe_gate(self):
        engine = IndependentEngine()
        registry = StrategyRegistry((engine,))
        decisions = registry.evaluate_selected(
            {"legacy_three_timeframe_confirmed": False},
            ("volume_trading",),
        )
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].status, "SIGNAL")
        self.assertEqual(engine.calls, 1)

    def test_unselected_engine_is_not_called(self):
        engine = IndependentEngine()
        registry = StrategyRegistry((engine,))
        registry.evaluate_selected({}, ("trend_pullback",))
        self.assertEqual(engine.calls, 0)

    def test_one_engine_cannot_mutate_another_engines_snapshot(self):
        class MutatingEngine:
            strategy_id = "breakout"
            enabled = True

            def evaluate(self, snapshot):
                snapshot["nested"]["value"] = "changed"
                return StrategyDecision(self.strategy_id, "WAIT", "mutated private copy")

        class ObservingEngine:
            strategy_id = "volume_trading"
            enabled = True

            def __init__(self):
                self.observed = None

            def evaluate(self, snapshot):
                self.observed = snapshot["nested"]["value"]
                return StrategyDecision(self.strategy_id, "WAIT", "observed input")

        observer = ObservingEngine()
        registry = StrategyRegistry((MutatingEngine(), observer))
        registry.evaluate_selected(
            {"nested": {"value": "original"}},
            ("breakout", "volume_trading"),
        )
        self.assertEqual(observer.observed, "original")

    def test_selected_ids_must_be_a_tuple_of_strings(self):
        registry = StrategyRegistry()
        with self.assertRaises(ValueError):
            registry.evaluate_selected({}, ["ict"])
        with self.assertRaises(ValueError):
            registry.evaluate_selected({}, (1,))

    def test_duplicate_engine_registration_is_rejected(self):
        engine = IndependentEngine()
        with self.assertRaises(ValueError):
            StrategyRegistry((engine, engine))

    def test_duplicate_or_unknown_selection_is_rejected(self):
        registry = StrategyRegistry()
        with self.assertRaises(ValueError):
            registry.evaluate_selected({}, ("ict", "ict"))
        with self.assertRaises(ValueError):
            registry.evaluate_selected({}, ("made_up",))


if __name__ == "__main__":
    unittest.main()
