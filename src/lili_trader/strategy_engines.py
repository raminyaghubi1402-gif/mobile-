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



def _positive_price(value: Any, field_name: str) -> float:
    from math import isfinite
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a finite positive price")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{field_name} must be a finite positive price") from None
    if not isfinite(result) or result <= 0:
        raise ValueError(f"{field_name} must be a finite positive price")
    return result


@dataclass(frozen=True)
class TrendPullbackEngine:
    """Conservative spot-long pullback engine using only closed-candle evidence.

    Snapshot contract:
      regime: "bullish" only
      spot_only: True
      pullback_active: True (15m pullback is explicitly identified upstream)
      support_zone: {kind: "support", lower: float, upper: float}
      latest_5m: {timestamp: int, open/high/low/close: float}
      structure_5m: {bias: "up", last_event: {kind, candle_index},
                     latest_candle_index: int}
    The caller must build this snapshot from closed, timestamp-validated candles.
    This engine deliberately fails closed when any evidence is absent.
    """

    strategy_id: str = "trend_pullback"
    enabled: bool = True
    minimum_reward_risk: float = 1.5
    maximum_candle_age_seconds: int = 900
    maximum_structure_event_age_bars: int = 36

    def evaluate(self, market_snapshot: Mapping[str, Any]) -> StrategyDecision:
        if not isinstance(market_snapshot, Mapping):
            raise ValueError("market_snapshot must be a mapping")
        from math import isfinite
        if (isinstance(self.minimum_reward_risk, bool)
                or not isinstance(self.minimum_reward_risk, (int, float))
                or not isfinite(self.minimum_reward_risk)
                or self.minimum_reward_risk < 1.5
                or type(self.maximum_candle_age_seconds) is not int
                or self.maximum_candle_age_seconds < 0
                or type(self.maximum_structure_event_age_bars) is not int
                or self.maximum_structure_event_age_bars < 0):
            raise ValueError("invalid reward/risk or candle freshness configuration")
        if type(self.enabled) is not bool or not self.enabled:
            return StrategyDecision(self.strategy_id, "DISABLED", "موتور غیرفعال است.")
        if market_snapshot.get("spot_only") is not True:
            return StrategyDecision(self.strategy_id, "WAIT",
                                    "این موتور فقط برای حالت نقدیِ بدون اهرم فعال است.")
        if market_snapshot.get("regime") != "bullish":
            return StrategyDecision(self.strategy_id, "WAIT",
                                    "رژیم صعودی معتبر نیست؛ خرید انجام نمی‌شود.")
        if market_snapshot.get("pullback_active") is not True:
            return StrategyDecision(self.strategy_id, "WAIT",
                                    "اصلاح ۱۵ دقیقه‌ای به‌صورت مستقل تأیید نشده است.")
        zone = market_snapshot.get("support_zone")
        candle = market_snapshot.get("latest_5m")
        structure = market_snapshot.get("structure_5m")
        if not all(isinstance(item, Mapping) for item in (zone, candle, structure)):
            return StrategyDecision(self.strategy_id, "WAIT",
                                    "ناحیه حمایت، کندل بسته‌شده یا ساختار ۵ دقیقه ناقص است.")
        if zone.get("kind") != "support":
            return StrategyDecision(self.strategy_id, "WAIT",
                                    "ناحیه معتبر حمایت در دسترس نیست.")
        try:
            lower = _positive_price(zone.get("lower"), "support lower")
            upper = _positive_price(zone.get("upper"), "support upper")
            open_ = _positive_price(candle.get("open"), "open")
            high = _positive_price(candle.get("high"), "high")
            low = _positive_price(candle.get("low"), "low")
            close = _positive_price(candle.get("close"), "close")
            timestamp = candle.get("timestamp")
            fetched_at = candle.get("fetched_at")
            latest_index = structure.get("latest_candle_index")
            event = structure.get("last_event")
            if lower > upper or low > min(open_, close) or high < max(open_, close) or low > high:
                raise ValueError("inconsistent zone/candle")
            if (type(timestamp) is not int or timestamp <= 0
                    or type(fetched_at) is not int or fetched_at <= 0):
                raise ValueError("closed candle timestamp/fetch time is invalid")
            candle_close_time = timestamp + 300
            if candle_close_time > fetched_at:
                raise ValueError("latest 5m candle is not closed yet")
            candle_age = fetched_at - candle_close_time
            if candle_age > self.maximum_candle_age_seconds:
                raise ValueError("latest 5m candle is stale")
            if type(latest_index) is not int or latest_index < 0:
                raise ValueError("latest closed-candle index is invalid")
            if not isinstance(event, Mapping):
                raise ValueError("latest structure event is missing")
            event_index = event.get("candle_index")
            if (type(event_index) is not int or event_index < 0
                    or event_index > latest_index
                    or latest_index - event_index > self.maximum_structure_event_age_bars):
                raise ValueError("structure event is stale or from a future candle")
            if event.get("kind") not in ("CHOCH_UP", "BOS_UP"):
                return StrategyDecision(self.strategy_id, "WAIT",
                                        "شکست صعودی معتبر با بسته‌شدن کندل ۵ دقیقه تأیید نشده است.")
            if structure.get("bias") != "up":
                return StrategyDecision(self.strategy_id, "WAIT",
                                        "جهت ساختار ۵ دقیقه هنوز صعودی نشده است.")
            if not (low <= upper and close >= lower):
                return StrategyDecision(self.strategy_id, "WAIT",
                                        "قیمت ناحیه حمایت را لمس و حفظ نکرده است.")
            if close <= open_:
                return StrategyDecision(self.strategy_id, "WAIT",
                                        "کندل تأییدی صعودی نیست.")
            stop = lower
            risk = close - stop
            if risk <= 0:
                return StrategyDecision(self.strategy_id, "WAIT",
                                        "حد ضرر معتبر زیر قیمت ورود تشکیل نشده است.")
            target = close + risk * self.minimum_reward_risk
            if not all(isfinite(x) for x in (stop, target)):
                raise ValueError("non-finite risk levels")
        except ValueError as exc:
            return StrategyDecision(self.strategy_id, "WAIT",
                                    f"داده یا شواهد نامعتبر است؛ ورود مسدود شد: {exc}")
        return StrategyDecision(
            self.strategy_id,
            "SIGNAL",
            "روند صعودی، اصلاح فعال، حفظ حمایت و کندل تأییدی صعودی پس از CHoCH/BOS معتبر و تازه تأیید شدند؛ فقط سیگنال Paper.",
            {
                "entry_reference": close,
                "stop_loss": stop,
                "take_profit": target,
                "minimum_reward_risk": self.minimum_reward_risk,
                "structure_event": event.get("kind"),
                "structure_event_candle_index": event_index,
                "market_data_timestamp": timestamp,
                "market_data_fetched_at": fetched_at,
                "candle_age_seconds": candle_age,
                "execution": "paper_only",
            },
        )

def build_default_strategy_registry() -> StrategyRegistry:
    """Return the implemented engines; unimplemented catalog entries stay disabled."""
    from .breakout_engine import BreakoutEngine
    from .breakout_retest_engine import BreakoutRetestEngine
    from .support_resistance_rejection_engine import SupportResistanceRejectionEngine
    return StrategyRegistry((
        TrendPullbackEngine(),
        BreakoutEngine(),
        BreakoutRetestEngine(),
        SupportResistanceRejectionEngine(),
    ))
