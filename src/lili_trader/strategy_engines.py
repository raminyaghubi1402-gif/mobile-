"""Independent strategy engine contracts and an explicit registry.

This module intentionally contains no trading rules. Strategy implementations
must be added one at a time after their entry, invalidation, exit and evidence
rules are specified and tested. No legacy multi-timeframe gate is imported.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol


STRATEGY_CATALOG = (
    "trend_pullback",
    "breakout",
    "breakout_retest",
    "support_resistance_rejection",
    "range_trading",
    "volume_trading",
    "ict",
)


@dataclass(frozen=True)
class StrategyDecision:
    """One engine's independent decision; WAIT/NO_SETUP are valid outcomes."""

    strategy_id: str
    status: str
    reason: str
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.strategy_id not in STRATEGY_CATALOG:
            raise ValueError("unknown strategy_id")
        if self.status not in {
            "NO_SETUP", "WAIT", "SIGNAL", "INVALIDATED", "DISABLED"
        }:
            raise ValueError("unsupported strategy decision status")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("decision reason is required")
        if not isinstance(self.evidence, Mapping):
            raise ValueError("decision evidence must be a mapping")


class StrategyEngine(Protocol):
    strategy_id: str
    enabled: bool

    def evaluate(self, market_snapshot: Mapping[str, Any]) -> StrategyDecision:
        """Evaluate only this strategy's own rules against a validated snapshot."""


class StrategyRegistry:
    """Dispatch only explicitly selected engines; never AND their entry rules."""

    def __init__(self, engines: tuple[StrategyEngine, ...] = ()) -> None:
        self._engines: dict[str, StrategyEngine] = {}
        for engine in engines:
            strategy_id = getattr(engine, "strategy_id", None)
            if strategy_id not in STRATEGY_CATALOG:
                raise ValueError("engine has an unknown strategy_id")
            if strategy_id in self._engines:
                raise ValueError("duplicate strategy_id")
            if type(getattr(engine, "enabled", None)) is not bool:
                raise ValueError("engine enabled flag must be boolean")
            if not callable(getattr(engine, "evaluate", None)):
                raise ValueError("engine must provide a callable evaluate method")
            self._engines[strategy_id] = engine

    @property
    def registered_ids(self) -> tuple[str, ...]:
        return tuple(self._engines)

    def evaluate_selected(
        self,
        market_snapshot: Mapping[str, Any],
        selected_ids: tuple[str, ...],
    ) -> tuple[StrategyDecision, ...]:
        if not isinstance(market_snapshot, Mapping):
            raise ValueError("market_snapshot must be a mapping")
        if not isinstance(selected_ids, tuple) or any(
            not isinstance(item, str) for item in selected_ids
        ):
            raise ValueError("selected_ids must be a tuple of strategy id strings")
        if len(set(selected_ids)) != len(selected_ids):
            raise ValueError("selected strategy ids must be unique")
        decisions = []
        for strategy_id in selected_ids:
            if strategy_id not in STRATEGY_CATALOG:
                raise ValueError("unknown selected strategy")
            engine = self._engines.get(strategy_id)
            if engine is None or not engine.enabled:
                decisions.append(StrategyDecision(
                    strategy_id=strategy_id,
                    status="DISABLED",
                    reason="این موتور هنوز پیاده‌سازی و تأیید نشده است.",
                ))
                continue
            # Each engine receives its own deep copy so accidental mutation by
            # one implementation cannot influence another engine's evaluation.
            try:
                engine_snapshot = deepcopy(dict(market_snapshot))
            except Exception as exc:
                raise ValueError("market_snapshot cannot be isolated safely") from exc
            decision = engine.evaluate(engine_snapshot)
            if not isinstance(decision, StrategyDecision):
                raise TypeError("strategy engine must return StrategyDecision")
            if decision.strategy_id != strategy_id:
                raise ValueError("engine returned a decision for another strategy")
            decisions.append(decision)
        return tuple(decisions)
