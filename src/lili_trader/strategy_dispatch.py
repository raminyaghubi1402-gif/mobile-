"""Dispatch market-regime candidates to independent strategy engines.

This layer translates candidate labels into engine IDs. It never combines
strategy-specific confirmations into a shared entry gate.
"""
from __future__ import annotations

from dataclasses import dataclass

from .market_regime import StrategyCandidate, select_strategy_candidates
from .strategy_engines import StrategyDecision, StrategyRegistry


_CANDIDATE_TO_ENGINE = {
    "trend_pullback_buy": "trend_pullback",
    "trend_pullback_sell": "trend_pullback",
    "support_rejection_buy": "support_resistance_rejection",
    "resistance_rejection_sell": "support_resistance_rejection",
    "breakout_retest_buy": "breakout_retest",
    "breakdown_retest_sell": "breakout_retest",
    "breakout": "breakout",
    "volume_trading": "volume_trading",
    "ict": "ict",
}
_NO_TRADE_CANDIDATES = {"no_trade_wait", "no_trade_range_policy"}


@dataclass(frozen=True)
class StrategyDispatchResult:
    """Candidates and each independent engine's result for one regime snapshot."""

    candidates: tuple[StrategyCandidate, ...]
    engine_ids: tuple[str, ...]
    decisions: tuple[StrategyDecision, ...]

    @property
    def no_engine_selected(self) -> bool:
        return not any(decision.status != "DISABLED" for decision in self.decisions)


def dispatch_regime_candidates(
    regime,
    market_snapshot,
    registry: StrategyRegistry,
) -> StrategyDispatchResult:
    """Select candidate engines from a regime and evaluate them independently.

    Candidate eligibility is not an entry signal. The registry returns DISABLED
    for engines not yet implemented or enabled; no legacy timeframe gate is
    applied here. Shared data/risk safety checks belong to their separate layers.
    """
    candidates = select_strategy_candidates(regime)
    selected_ids = []
    for candidate in candidates:
        if not candidate.eligible or candidate.name in _NO_TRADE_CANDIDATES:
            continue
        engine_id = _CANDIDATE_TO_ENGINE.get(candidate.name)
        if engine_id is None:
            # Fail closed: do not silently dispatch an unmapped strategy.
            raise ValueError(f"unmapped strategy candidate: {candidate.name}")
        if engine_id not in selected_ids:
            selected_ids.append(engine_id)

    engine_ids = tuple(selected_ids)
    decisions = registry.evaluate_selected(market_snapshot, engine_ids)
    return StrategyDispatchResult(candidates, engine_ids, decisions)
