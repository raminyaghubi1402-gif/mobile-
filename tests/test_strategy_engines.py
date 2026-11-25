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



class TrendPullbackEngineTests(unittest.TestCase):
    def setUp(self):
        from lili_trader.strategy_engines import TrendPullbackEngine
        self.engine = TrendPullbackEngine()
        self.snapshot = {
            "spot_only": True,
            "regime": "bullish",
            "pullback_active": True,
            "support_zone": {"kind": "support", "lower": 98.0, "upper": 100.0},
            "latest_5m": {
                "timestamp": 1_800_000_000,
                "fetched_at": 1_800_000_301,
                "open": 100.0, "high": 103.0, "low": 99.0, "close": 102.0,
            },
            "structure_5m": {
                "bias": "up",
                "last_event": {"kind": "CHOCH_UP", "candle_index": 99},
                "latest_candle_index": 99,
            },
        }

    def test_valid_closed_candle_setup_emits_paper_signal_with_levels(self):
        decision = self.engine.evaluate(self.snapshot)
        self.assertEqual(decision.status, "SIGNAL")
        self.assertEqual(decision.evidence["execution"], "paper_only")
        self.assertEqual(decision.evidence["stop_loss"], 98.0)
        self.assertEqual(decision.evidence["take_profit"], 108.0)

    def test_wick_only_or_stale_structure_event_cannot_trigger(self):
        self.snapshot["structure_5m"]["last_event"]["candle_index"] = 98
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")

    def test_no_signal_in_range_or_bearish_regime(self):
        self.snapshot["regime"] = "range"
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")
        self.snapshot["regime"] = "bearish"
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")

    def test_spot_only_and_pullback_requirements_are_mandatory(self):
        self.snapshot["spot_only"] = False
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")
        self.snapshot["spot_only"] = True
        self.snapshot["pullback_active"] = False
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")

    def test_bearish_confirmation_candle_is_rejected(self):
        self.snapshot["latest_5m"]["close"] = 99.0
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")

    def test_missing_zone_or_structure_fails_closed(self):
        self.snapshot["support_zone"] = None
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")

    def test_unclosed_and_stale_candles_are_rejected(self):
        self.snapshot["latest_5m"]["fetched_at"] = 1_800_000_299
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")
        self.snapshot["latest_5m"]["fetched_at"] = 1_800_002_000
        self.assertEqual(self.engine.evaluate(self.snapshot).status, "WAIT")



if __name__ == "__main__":
    unittest.main()
