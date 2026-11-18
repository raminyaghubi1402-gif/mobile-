import unittest

from lili_trader.market_regime import MarketRegime
from lili_trader.strategy_dispatch import dispatch_regime_candidates
from lili_trader.strategy_engines import StrategyDecision, StrategyRegistry


class StubEngine:
    def __init__(self, strategy_id, status):
        self.strategy_id = strategy_id
        self.enabled = True
        self.status = status
        self.calls = 0
        self.seen_snapshot = None

    def evaluate(self, snapshot):
        self.calls += 1
        self.seen_snapshot = snapshot
        return StrategyDecision(
            self.strategy_id, self.status, f"{self.strategy_id} stub decision"
        )


def uptrend_regime(candidates):
    return MarketRegime(
        trend="up",
        volatility="normal",
        structure_quality="aligned",
        range_position="lower",
        allowed_strategies=tuple(candidates),
        confidence="moderate",
        reason="test regime",
    )


class StrategyDispatchTests(unittest.TestCase):
    def test_volume_signal_is_not_blocked_by_another_engine_waiting(self):
        trend = StubEngine("trend_pullback", "WAIT")
        volume = StubEngine("volume_trading", "SIGNAL")
        registry = StrategyRegistry((trend, volume))

        result = dispatch_regime_candidates(
            uptrend_regime(("trend_pullback_buy", "volume_trading")),
            {"legacy_three_timeframe_confirmed": False},
            registry,
        )

        self.assertEqual(result.engine_ids, ("trend_pullback", "volume_trading"))
        self.assertEqual([d.status for d in result.decisions], ["WAIT", "SIGNAL"])
        self.assertEqual(volume.calls, 1)
        self.assertIsNotNone(volume.seen_snapshot)
        self.assertFalse(volume.seen_snapshot["legacy_three_timeframe_confirmed"])

    def test_explicit_generic_candidates_dispatch_independently(self):
        engines = (
            StubEngine("breakout", "WAIT"),
            StubEngine("volume_trading", "WAIT"),
            StubEngine("ict", "DISABLED"),
        )
        result = dispatch_regime_candidates(
            uptrend_regime(("breakout", "volume_trading", "ict")),
            {},
            StrategyRegistry(engines),
        )
        self.assertEqual(result.engine_ids, ("breakout", "volume_trading", "ict"))
        self.assertEqual([e.calls for e in engines], [1, 1, 1])

    def test_range_policy_selects_no_engine_even_if_volume_is_listed(self):
        range_regime = MarketRegime(
            trend="range",
            volatility="normal",
            structure_quality="mixed",
            range_position="middle",
            allowed_strategies=("volume_trading",),
            confidence="low",
            reason="test range",
        )
        volume = StubEngine("volume_trading", "SIGNAL")
        result = dispatch_regime_candidates(range_regime, {}, StrategyRegistry((volume,)))
        self.assertEqual(result.engine_ids, ())
        self.assertEqual(result.decisions, ())
        self.assertTrue(result.no_engine_selected)
        self.assertEqual(volume.calls, 0)

    def test_unimplemented_candidate_is_reported_disabled_not_fabricated(self):
        result = dispatch_regime_candidates(
            uptrend_regime(("volume_trading",)), {}, StrategyRegistry()
        )
        self.assertEqual(result.engine_ids, ("volume_trading",))
        self.assertEqual([d.status for d in result.decisions], ["DISABLED"])
        self.assertTrue(result.no_engine_selected is False)


if __name__ == "__main__":
    unittest.main()
