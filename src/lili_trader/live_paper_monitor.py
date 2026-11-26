"""Nobitex live-candle paper monitor; public data only, never places orders.

The monitor evaluates fully closed, fresh, contiguous candles and records
idempotent decisions. Paper entries occur at the next 5m candle open. A small
JSON state file carries pending signals and open simulated positions between
scheduled workflow runs.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from math import isfinite
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .decision_journal import DecisionJournal, DecisionJournalError
from .engine_snapshot_builder import build_independent_engine_snapshots
from .market_regime import apply_primary_trend, classify_market_regime, select_strategy_candidates
from .market_structure import analyze_market_structure
from .nobitex_data import NobitexAdapter, NobitexCandle
from .price_action_trend import classify_price_action_trend
from .nobitex_limits import NOBITEX_BASE_SPOT_FEE_RATE_IRT, NOBITEX_MINIMUM_SPOT_ORDER_IRT
from .risk_management import propose_structural_trailing_stop
from .strategy_engines import StrategyDecision, build_default_strategy_registry
from .support_resistance import find_support_resistance


SYMBOLS = ("BTCIRT", "ETHIRT")
RESOLUTIONS = {"5m": ("5", 300), "15m": ("15", 900), "1h": ("60", 3600)}
ENGINE_VERSION = "trend_pullback_v1"
PAPER_EQUITY_USDT = 10.0
RISK_FRACTION = 0.005
FEE_RATE = NOBITEX_BASE_SPOT_FEE_RATE_IRT
SLIPPAGE_RATE = 0.001


class LivePaperMonitorError(RuntimeError):
    """A live observation could not be safely evaluated."""


def _iso(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def latest_contiguous_closed_segment(
    candles: list[NobitexCandle],
    *,
    resolution_seconds: int,
    now: int,
    minimum_bars: int,
    maximum_age_seconds: int,
) -> list[NobitexCandle]:
    """Return the latest closed, contiguous segment; never bridge a missing bar."""
    if type(now) is not int or now <= 0:
        raise ValueError("now must be a positive integer timestamp")
    closed = sorted(
        (c for c in candles if c.timestamp + resolution_seconds <= now),
        key=lambda c: c.timestamp,
    )
    unique: list[NobitexCandle] = []
    for candle in closed:
        if unique and candle.timestamp == unique[-1].timestamp:
            if candle != unique[-1]:
                raise LivePaperMonitorError("duplicate timestamp has conflicting OHLCV")
            continue
        if unique and candle.timestamp < unique[-1].timestamp:
            raise LivePaperMonitorError("candles are not chronological")
        unique.append(candle)
    if not unique:
        raise LivePaperMonitorError("Nobitex returned no fully closed candles")
    segment_start = 0
    for index in range(1, len(unique)):
        delta = unique[index].timestamp - unique[index - 1].timestamp
        if delta != resolution_seconds:
            segment_start = index
    segment = unique[segment_start:]
    age = now - (segment[-1].timestamp + resolution_seconds)
    if age < 0 or age > maximum_age_seconds:
        raise LivePaperMonitorError(f"latest closed candle is stale ({age}s)")
    if len(segment) < minimum_bars:
        raise LivePaperMonitorError(
            f"latest continuous segment has {len(segment)} bars; {minimum_bars} required"
        )
    return segment


def _zone_dict(zone) -> dict[str, float | str]:
    return {"kind": zone.kind, "lower": float(zone.lower), "upper": float(zone.upper)}


def build_trend_pullback_snapshot(
    hourly: list[NobitexCandle],
    fifteen: list[NobitexCandle],
    five: list[NobitexCandle],
    *,
    now: int,
) -> dict[str, Any]:
    """Build an evidence-only snapshot from validated, closed Nobitex candles."""
    trend_1h = classify_price_action_trend(hourly, pivot_window=2, min_swings=2)
    trend_15m = classify_price_action_trend(fifteen, pivot_window=2, min_swings=2)
    regime = classify_market_regime(fifteen, trend_15m, volatility_window=20)
    structure = analyze_market_structure(five, pivot_window=2)

    supports = find_support_resistance(
        fifteen, pivot_window=3, atr_period=14, tolerance_atr=0.5,
        min_touches=2, max_zones=4,
    )
    latest = five[-1]
    support_candidates = [
        zone for zone in supports
        if zone.kind == "support" and zone.midpoint <= latest.close
    ]
    support = min(
        support_candidates,
        key=lambda zone: latest.close - zone.midpoint,
        default=None,
    )
    event = structure.last_event
    event_age = (len(five) - 1 - event.candle_index) if event is not None else None
    pullback_active = bool(
        support is not None
        and latest.low <= support.upper
        and latest.close >= support.lower
    )
    structure_event = None
    if event is not None:
        structure_event = {
            "kind": event.kind,
            "candle_index": event.candle_index,
            "timestamp": event.timestamp,
            "level": event.level,
            "close": event.close,
        }

    primary_regime = apply_primary_trend(
        regime, trend_1h.direction, trend_15m.direction
    )
    bullish = (
        primary_regime.trend == "up"
        and primary_regime.volatility in {"low", "normal"}
        and structure.bias == "up"
    )
    return {
        "spot_only": True,
        "regime": "bullish" if bullish else regime.trend,
        "pullback_active": pullback_active,
        "support_zone": _zone_dict(support) if support is not None else None,
        "latest_5m": {
            "timestamp": int(latest.timestamp),
            "fetched_at": int(now),
            "open": float(latest.open),
            "high": float(latest.high),
            "low": float(latest.low),
            "close": float(latest.close),
        },
        "structure_5m": {
            "bias": structure.bias,
            "last_event": structure_event,
            "latest_candle_index": len(five) - 1,
            "event_age_bars": event_age,
        },
        "context": {
            "trend_1h": trend_1h.direction,
            "trend_15m": trend_15m.direction,
            "volatility_15m": regime.volatility,
            "regime_reason": primary_regime.reason,
            "primary_trend": primary_regime.trend,
            "primary_trend_timeframe": "15m" if trend_1h.direction == "range" else "1h",
            "structure_reason": structure.reason_fa,
            "support_zone_touches": support.touches if support else 0,
        },
    }


class PaperState:
    """Atomic local state for engine-owned pending entries and paper positions."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.data: dict[str, Any] = {"schema_version": 2, "pending": {}, "positions": {}}
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise LivePaperMonitorError("paper state is unreadable; refusing to reset it") from exc
            if (not isinstance(loaded, dict) or loaded.get("schema_version") not in {1, 2}
                    or not isinstance(loaded.get("pending"), dict)
                    or not isinstance(loaded.get("positions"), dict)):
                raise LivePaperMonitorError("paper state schema is invalid; refusing to reset it")
            if loaded["schema_version"] == 1:
                # Preserve old positions, but reject ambiguous or contradictory
                # symbol/engine identities before moving them into schema v2 slots.
                for collection in ("pending", "positions"):
                    migrated = {}
                    for key, record in loaded[collection].items():
                        if not isinstance(key, str) or not key or not isinstance(record, dict):
                            raise LivePaperMonitorError("legacy paper state record is invalid")
                        key_symbol, separator, key_strategy = key.partition("::")
                        if separator:
                            if key_symbol not in SYMBOLS or not key_strategy:
                                raise LivePaperMonitorError("legacy paper state slot key is invalid")
                            symbol = record.get("symbol", key_symbol)
                            strategy = record.get("strategy", key_strategy)
                            if symbol != key_symbol or strategy != key_strategy:
                                raise LivePaperMonitorError("legacy paper state slot identity is inconsistent")
                        else:
                            if key not in SYMBOLS:
                                raise LivePaperMonitorError("legacy paper state symbol key is invalid")
                            symbol = record.get("symbol", key)
                            strategy = record.get("strategy", "legacy")
                            if symbol != key:
                                raise LivePaperMonitorError("legacy paper state symbol does not match its key")
                        if not isinstance(strategy, str) or not strategy:
                            raise LivePaperMonitorError("legacy paper state strategy is invalid")
                        slot = f"{symbol}::{strategy}"
                        if slot in migrated:
                            raise LivePaperMonitorError("legacy paper state contains duplicate engine slots")
                        migrated[slot] = {**record, "symbol": symbol, "strategy": strategy}
                    loaded[collection] = migrated
                loaded["schema_version"] = 2
            self.data = loaded
            self._validate_records()

    def _validate_records(self) -> None:
        if (not isinstance(self.data, dict) or self.data.get("schema_version") != 2
                or not isinstance(self.data.get("pending"), dict)
                or not isinstance(self.data.get("positions"), dict)):
            raise LivePaperMonitorError("paper state schema is invalid; refusing to save it")
        for collection in ("pending", "positions"):
            for key, record in self.data[collection].items():
                if not isinstance(record, dict):
                    raise LivePaperMonitorError("paper state engine record is invalid")
                symbol = _record_symbol(key, record)
                key_symbol, separator, key_strategy = key.partition("::")
                if (not separator or key_symbol != symbol or not key_strategy
                        or record.get("strategy") != key_strategy):
                    raise LivePaperMonitorError("paper state engine slot identity is invalid")

    def save(self) -> None:
        self._validate_records()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.data, sort_keys=True, ensure_ascii=False, allow_nan=False, indent=2)
        fd, temporary = tempfile.mkstemp(prefix=self.path.name + ".", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(payload + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def _engine_state_key(symbol: str, strategy_id: str) -> str:
    if symbol not in SYMBOLS or not isinstance(strategy_id, str) or not strategy_id:
        raise LivePaperMonitorError("paper engine slot identity is invalid")
    return f"{symbol}::{strategy_id}"


def _record_symbol(key: str, record: dict[str, Any]) -> str:
    if not isinstance(key, str) or not key or not isinstance(record, dict):
        raise LivePaperMonitorError("paper state engine slot is invalid")
    key_symbol, separator, key_strategy = key.partition("::")
    if separator:
        if key_symbol not in SYMBOLS or not key_strategy:
            raise LivePaperMonitorError("paper state engine slot key is invalid")
        symbol = record.get("symbol")
        strategy = record.get("strategy")
        if symbol != key_symbol or (strategy is not None and strategy != key_strategy):
            raise LivePaperMonitorError("paper state engine slot identity does not match its record")
        return key_symbol
    if key not in SYMBOLS:
        raise LivePaperMonitorError("paper state contains an unknown symbol")
    symbol = record.get("symbol", key)
    if symbol != key:
        raise LivePaperMonitorError("legacy paper state symbol does not match its key")
    return key


def _validated_signal_levels(decision: StrategyDecision) -> dict[str, float]:
    """Validate one engine's execution levels without affecting other engines."""
    evidence = decision.evidence
    values: dict[str, float] = {}
    for name in ("entry_reference", "stop_loss", "take_profit"):
        raw = evidence.get(name)
        if isinstance(raw, bool):
            raise ValueError(f"{name} is not a valid price")
        try:
            value = float(raw)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} is missing or invalid") from exc
        if not isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be finite and positive")
        values[name] = value
    entry, stop, target = (
        values["entry_reference"], values["stop_loss"], values["take_profit"]
    )
    if not stop < entry < target:
        raise ValueError("signal prices are not ordered for a spot BUY")
    if (target - entry) / (entry - stop) + 1e-9 < 1.5:
        raise ValueError("signal reward/risk is below the 1.5 minimum")
    return values


def _decision_id(
    symbol: str, candle_timestamp: int, strategy_id: str = "trend_pullback"
) -> str:
    version = ENGINE_VERSION if strategy_id == "trend_pullback" else f"{strategy_id}_v1"
    raw = f"{symbol}|{version}|{candle_timestamp}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _manage_position(
    symbol: str,
    candles: list[NobitexCandle],
    state: PaperState,
    journal: DecisionJournal,
    *,
    now: int,
    state_key: str | None = None,
) -> str:
    position_key = state_key or symbol
    position = state.data["positions"].get(position_key)
    if not position:
        return "no_open_position"
    expected = int(position["last_processed_timestamp"]) + 300
    stop_updates = 0
    for candle_index, candle in enumerate(candles):
        if candle.timestamp < expected:
            continue
        if candle.timestamp != expected:
            return f"position_paused_data_gap_expected_{expected}_got_{candle.timestamp}"
        if candle.timestamp + 300 > now:
            return f"position_paused_unclosed_candle_{candle.timestamp}"
        # The stop in force at the start of this candle is checked first.
        # A newly confirmed trailing stop can only apply to later candles.
        stop_hit = candle.low <= float(position["stop_loss"])
        target_hit = candle.high >= float(position["take_profit"])
        exit_reason = "stop_loss" if stop_hit else ("take_profit" if target_hit else None)
        if exit_reason is not None:
            raw_exit = float(position["stop_loss"] if stop_hit else position["take_profit"])
            exit_fill = raw_exit * (1 - SLIPPAGE_RATE)
            quantity = float(position["quantity"])
            raw_entry = float(position["raw_entry_price"])
            entry_fill = float(position["entry_fill_price"])
            gross = (raw_exit - raw_entry) * quantity
            fees = (entry_fill + exit_fill) * quantity * FEE_RATE
            slippage = ((entry_fill - raw_entry) + (raw_exit - exit_fill)) * quantity
            net = gross - fees - slippage
            expected_outcome = {
                "trade_id": position["trade_id"],
                "side": "BUY",
                "entry_at": _iso(int(position["entry_timestamp"])),
                "exit_at": _iso(candle.timestamp + 300),
                "gross_pnl_quote": gross,
                "fees_quote": fees,
                "slippage_quote": slippage,
                "net_pnl_quote": net,
                "exit_reason": exit_reason,
            }
            prior_outcomes = [
                event for event in journal.events()
                if event.event_type == "OUTCOME"
                and event.decision_id == position["decision_id"]
            ]
            if prior_outcomes:
                # A previous run may have appended the outcome but stopped before
                # the updated state was cached. Reconcile that exact event instead
                # of appending a duplicate or leaving the position stuck forever.
                if len(prior_outcomes) != 1:
                    raise LivePaperMonitorError(
                        "paper position has multiple journal outcomes; refusing reconciliation"
                    )
                prior = prior_outcomes[0].payload
                for key, expected_value in expected_outcome.items():
                    actual_value = prior.get(key)
                    if isinstance(expected_value, (int, float)):
                        try:
                            actual_number = float(actual_value)
                        except (TypeError, ValueError, OverflowError) as exc:
                            raise LivePaperMonitorError(
                                "existing journal outcome does not match paper position"
                            ) from exc
                        if (not isfinite(actual_number)
                                or abs(actual_number - expected_value)
                                > max(1e-8, abs(expected_value) * 1e-9)):
                            raise LivePaperMonitorError(
                                "existing journal outcome does not match paper position"
                            )
                    elif actual_value != expected_value:
                        raise LivePaperMonitorError(
                            "existing journal outcome does not match paper position"
                        )
            else:
                journal.append_outcome(
                    decision_id=position["decision_id"],
                    occurred_at=_iso(now),
                    **expected_outcome,
                )
            del state.data["positions"][position_key]
            return f"closed_{exit_reason}_net_{net:.2f}_IRT"

        # Raise the stop only after a right-side-confirmed higher low exists.
        # The risk manager requires a closed, contiguous sequence including
        # the entry candle and rejects any candidate already traded through.
        if candle.timestamp > int(position["entry_timestamp"]):
            update = propose_structural_trailing_stop(
                candles[:candle_index + 1],
                entry_timestamp=int(position["entry_timestamp"]),
                entry_fill_price=float(position["entry_fill_price"]),
                current_stop=float(position["stop_loss"]),
                fee_rate=FEE_RATE,
                slippage_rate=SLIPPAGE_RATE,
            )
            if update is not None:
                position["stop_loss"] = update.stop_loss
                position["stop_updated_at"] = candle.timestamp
                position["stop_update_reason"] = "confirmed_higher_low_after_fee_adjusted_breakeven"
                position["last_trailing_swing_timestamp"] = update.swing_timestamp
                stop_updates += 1

        position["last_processed_timestamp"] = candle.timestamp
        expected += 300

    state.data["positions"][position_key] = position
    if stop_updates:
        return f"position_open_trailing_stop_raised_{stop_updates}_times_to_{float(position['stop_loss']):.2f}"
    return "position_open"


def _reserved_quote(
    state: PaperState, *, exclude_pending_symbol: str | None = None,
    equity_quote: float, exclude_pending_key: str | None = None,
) -> float:
    """Estimate cash committed to all other open and pending spot positions.

    Open positions reserve their entry cost plus entry fee. Other pending
    entries reserve their risk-sized notional using the signal reference price.
    An older/corrupt pending record without that reference blocks new allocation
    rather than allowing the shared paper account to over-allocate cash.
    """
    if not isfinite(equity_quote) or equity_quote <= 0:
        raise LivePaperMonitorError("paper equity quote must be finite and positive")
    reserved = 0.0
    for key, position in state.data["positions"].items():
        if not isinstance(position, dict):
            raise LivePaperMonitorError("paper position state is invalid")
        _record_symbol(key, position)
        try:
            entry_fill = float(position["entry_fill_price"])
            quantity = float(position["quantity"])
            entry_fee = float(position.get(
                "entry_fee_quote", entry_fill * quantity * FEE_RATE
            ))
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise LivePaperMonitorError("paper position allocation is invalid") from exc
        if (not all(isfinite(value) for value in (entry_fill, quantity, entry_fee))
                or entry_fill <= 0 or quantity <= 0 or entry_fee < 0):
            raise LivePaperMonitorError("paper position allocation is non-finite or non-positive")
        reserved += entry_fill * quantity + entry_fee

    for key, pending in state.data["pending"].items():
        pending_symbol = _record_symbol(key, pending) if isinstance(pending, dict) else None
        if key == exclude_pending_key or (
            exclude_pending_key is None and exclude_pending_symbol == pending_symbol
        ):
            continue
        if pending_symbol not in SYMBOLS:
            raise LivePaperMonitorError("pending paper allocation is invalid")
        try:
            reference = float(pending["entry_reference"])
            stop = float(pending["stop_loss"])
            risk_budget = float(pending["risk_quote"])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise LivePaperMonitorError(
                "pending paper allocation lacks a valid entry reference"
            ) from exc
        if (not all(isfinite(value) for value in (reference, stop, risk_budget))
                or reference <= stop or stop <= 0 or risk_budget <= 0):
            raise LivePaperMonitorError("pending paper allocation is invalid")
        estimated_fill = reference * (1 + SLIPPAGE_RATE)
        estimated_stop_fill = stop * (1 - SLIPPAGE_RATE)
        unit_risk = (
            estimated_fill - estimated_stop_fill
            + (estimated_fill + estimated_stop_fill) * FEE_RATE
        )
        if not isfinite(unit_risk) or unit_risk <= 0:
            raise LivePaperMonitorError("pending paper allocation has invalid unit risk")
        quantity_by_risk = risk_budget / unit_risk
        quantity_by_cash = equity_quote / (
            estimated_fill * (1 + FEE_RATE)
        )
        reserved += min(quantity_by_risk, quantity_by_cash) * estimated_fill * (
            1 + FEE_RATE
        )

    if not isfinite(reserved) or reserved < 0:
        raise LivePaperMonitorError("total paper cash reservation is invalid")
    return min(reserved, equity_quote)


def _committed_risk_quote(state: PaperState, *, symbol: str | None = None) -> float:
    """Return risk assigned to all slots, optionally scoped to one symbol."""
    committed = 0.0
    for key, position in state.data["positions"].items():
        if not isinstance(position, dict):
            raise LivePaperMonitorError("paper position risk state is invalid")
        if symbol is not None and _record_symbol(key, position) != symbol:
            continue
        try:
            risk = position.get("risk_quote")
            if risk is None:
                entry = float(position["entry_fill_price"])
                stop = float(position["stop_loss"]) * (1 - SLIPPAGE_RATE)
                quantity = float(position["quantity"])
                risk = quantity * (
                    entry - stop + (entry + stop) * FEE_RATE
                )
            risk = float(risk)
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise LivePaperMonitorError("open position risk allocation is invalid") from exc
        if not isfinite(risk) or risk < 0:
            raise LivePaperMonitorError("open position risk allocation is invalid")
        committed += risk

    for key, pending in state.data["pending"].items():
        if not isinstance(pending, dict):
            raise LivePaperMonitorError("pending risk state is invalid")
        if symbol is not None and _record_symbol(key, pending) != symbol:
            continue
        try:
            risk = float(pending["risk_quote"])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise LivePaperMonitorError("pending risk allocation is invalid") from exc
        if not isfinite(risk) or risk <= 0:
            raise LivePaperMonitorError("pending risk allocation is invalid")
        committed += risk

    if not isfinite(committed) or committed < 0:
        raise LivePaperMonitorError("total committed paper risk is invalid")
    return committed


def _activate_pending(
    symbol: str,
    candles: list[NobitexCandle],
    state: PaperState,
    *,
    equity_quote: float,
    state_key: str | None = None,
) -> str:
    if not isfinite(equity_quote) or equity_quote <= 0:
        raise LivePaperMonitorError("paper equity quote must be finite and positive")
    pending_key = state_key or symbol
    pending = state.data["pending"].get(pending_key)
    if not pending or pending_key in state.data["positions"]:
        return "no_pending_entry"
    expected = int(pending["signal_timestamp"]) + 300
    next_candle = next((c for c in candles if c.timestamp >= expected), None)
    if next_candle is None:
        return "pending_next_5m_candle"
    if next_candle.timestamp != expected:
        del state.data["pending"][pending_key]
        return "pending_entry_expired_missing_next_candle"

    raw_entry = float(next_candle.open)
    entry_fill = raw_entry * (1 + SLIPPAGE_RATE)
    stop = float(pending["stop_loss"])
    target = float(pending["take_profit"])
    stop_fill = stop * (1 - SLIPPAGE_RATE)
    unit_risk = (entry_fill - stop_fill) + (entry_fill + stop_fill) * FEE_RATE
    actual_reward = target - entry_fill
    actual_risk = entry_fill - stop
    actual_rr = actual_reward / actual_risk if actual_risk > 0 else 0.0
    if (unit_risk <= 0 or raw_entry <= stop or raw_entry >= target
            or actual_rr + 1e-9 < 1.5):
        del state.data["pending"][pending_key]
        return "pending_entry_cancelled_gap_or_invalid_risk"
    risk_budget = float(pending["risk_quote"])
    if not isfinite(risk_budget) or risk_budget <= 0:
        raise LivePaperMonitorError("pending entry risk budget is invalid")
    available_cash = max(
        0.0, equity_quote - _reserved_quote(
            state, equity_quote=equity_quote, exclude_pending_key=pending_key
        )
    )
    quantity_by_risk = risk_budget / unit_risk
    quantity_by_cash = available_cash / (entry_fill * (1 + FEE_RATE))
    quantity = min(quantity_by_risk, quantity_by_cash)
    if quantity <= 0:
        del state.data["pending"][pending_key]
        return "pending_entry_cancelled_zero_quantity"
    entry_notional = entry_fill * quantity
    if entry_notional + 1e-9 < NOBITEX_MINIMUM_SPOT_ORDER_IRT:
        del state.data["pending"][pending_key]
        return (
            "pending_entry_cancelled_below_minimum_order_"
            f"{NOBITEX_MINIMUM_SPOT_ORDER_IRT}"
        )
    state.data["positions"][pending_key] = {
        "symbol": symbol,
        "strategy": pending.get("strategy", "legacy"),
        "risk_quote": risk_budget,
        "trade_id": f"paper-{pending['decision_id']}",
        "decision_id": pending["decision_id"],
        "entry_timestamp": next_candle.timestamp,
        "last_processed_timestamp": next_candle.timestamp - 300,
        "raw_entry_price": raw_entry,
        "entry_fill_price": entry_fill,
        "quantity": quantity,
        "stop_loss": stop,
        "take_profit": target,
        "entry_fee_quote": entry_fill * quantity * FEE_RATE,
        "execution": "paper_only",
    }
    del state.data["pending"][pending_key]
    return f"paper_entry_at_next_open_{raw_entry:.2f}_qty_{quantity:.8f}"


def run_live_paper_monitor(
    *,
    journal_path: str | Path,
    state_path: str | Path,
    adapter: NobitexAdapter | None = None,
    now: int | None = None,
) -> dict[str, Any]:
    """Fetch fresh public candles, record independent decisions, and manage paper trades."""
    import time

    observed_at = int(time.time()) if now is None else now
    if type(observed_at) is not int or observed_at <= 0:
        raise ValueError("now must be a positive Unix timestamp")
    client = adapter or NobitexAdapter()
    journal = DecisionJournal(journal_path)
    state = PaperState(state_path)
    registry = build_default_strategy_registry()
    reports: dict[str, Any] = {}
    try:
        usdt_irt_rate = client.fetch_usdt_irt_rate(now=observed_at)
        paper_equity_quote = PAPER_EQUITY_USDT * usdt_irt_rate
        capital_rate_error = None
    except Exception as exc:
        # Existing positions remain manageable, but no new allocation may use
        # a stale or guessed conversion rate.
        usdt_irt_rate = None
        paper_equity_quote = None
        capital_rate_error = f"{type(exc).__name__}: {exc}"

    for symbol in SYMBOLS:
        try:
            fetched = {
                "1h": client.fetch_history(symbol, "60", 100),
                "15m": client.fetch_history(symbol, "15", 200),
                "5m": client.fetch_history(symbol, "5", 500),
            }
            validated = {
                "1h": latest_contiguous_closed_segment(
                    fetched["1h"], resolution_seconds=3600, now=observed_at,
                    minimum_bars=30, maximum_age_seconds=7200,
                ),
                "15m": latest_contiguous_closed_segment(
                    fetched["15m"], resolution_seconds=900, now=observed_at,
                    minimum_bars=30, maximum_age_seconds=1800,
                ),
                "5m": latest_contiguous_closed_segment(
                    fetched["5m"], resolution_seconds=300, now=observed_at,
                    minimum_bars=40, maximum_age_seconds=900,
                ),
            }
            position_status: dict[str, str] = {}
            for position_key, position_record in list(state.data["positions"].items()):
                if isinstance(position_record, dict) and _record_symbol(position_key, position_record) == symbol:
                    try:
                        position_status[position_key] = _manage_position(
                            symbol, validated["5m"], state, journal, now=observed_at,
                            state_key=position_key,
                        )
                    except Exception as exc:
                        # Corruption in one engine-owned position must not stop
                        # management of another engine's independent position.
                        position_status[position_key] = (
                            f"position_isolated_error_{type(exc).__name__}"
                        )

            pending_status: dict[str, str] = {}
            for pending_key, pending_record in list(state.data["pending"].items()):
                if isinstance(pending_record, dict) and _record_symbol(pending_key, pending_record) == symbol:
                    try:
                        pending_status[pending_key] = (
                            _activate_pending(
                                symbol, validated["5m"], state, equity_quote=paper_equity_quote,
                                state_key=pending_key,
                            )
                            if paper_equity_quote is not None
                            else "pending_entry_paused_usdt_irt_rate_unavailable"
                        )
                    except Exception as exc:
                        pending_status[pending_key] = (
                            f"pending_isolated_error_{type(exc).__name__}"
                        )
            engine_snapshots = build_independent_engine_snapshots(
                validated["1h"], validated["15m"], validated["5m"], now=observed_at
            )
            snapshot = engine_snapshots["trend_pullback"]
            regime_15m = classify_market_regime(
                validated["15m"],
                classify_price_action_trend(validated["15m"], pivot_window=2, min_swings=2),
                volatility_window=20,
            )
            observed_trend_1h = engine_snapshots["breakout"]["trend_1h"]
            observed_trend_15m = engine_snapshots["breakout"]["trend_15m"]
            regime = apply_primary_trend(
                regime_15m, observed_trend_1h, observed_trend_15m
            )
            snapshot["context"]["trend_1h"] = observed_trend_1h
            snapshot["context"]["trend_15m"] = observed_trend_15m
            snapshot["context"]["primary_trend"] = regime.trend
            snapshot["context"]["primary_trend_timeframe"] = (
                "15m" if observed_trend_1h == "range" else "1h"
            )
            candidates = select_strategy_candidates(regime, allow_short=False)
            eligible_ids = {candidate.name for candidate in candidates if candidate.eligible}
            selected = tuple(
                engine_id for engine_id in registry.registered_ids
                if regime.trend == "up" and regime.volatility in {"low", "normal"}
                and engine_id in eligible_ids
            )

            engine_decisions = []
            for engine_id in selected:
                try:
                    engine_snapshot = engine_snapshots.get(engine_id)
                    if not isinstance(engine_snapshot, dict):
                        raise ValueError("engine-specific snapshot is unavailable")
                    engine_decisions.append(
                        registry.evaluate_selected(engine_snapshot, (engine_id,))[0]
                    )
                except Exception as exc:
                    engine_decisions.append(StrategyDecision(
                        strategy_id=engine_id,
                        status="WAIT",
                        reason="شواهد این موتور در این نوبت مستقل از سایر موتورها در دسترس نیست.",
                        evidence={
                            "engine_isolated": True,
                            "engine_error_type": type(exc).__name__,
                            "engine_error": str(exc)[:300],
                        },
                    ))
            latest_timestamp = int(validated["5m"][-1].timestamp)
            known_decisions = {
                event.decision_id for event in journal.events()
                if event.event_type == "DECISION"
            }
            occupied_slots = {
                key for key, record in (
                    list(state.data["positions"].items()) + list(state.data["pending"].items())
                )
                if isinstance(record, dict) and _record_symbol(key, record) == symbol
            }
            signal_levels: dict[str, dict[str, float]] = {}
            signal_validation_errors: dict[str, str] = {}
            for decision in engine_decisions:
                if decision.status != "SIGNAL":
                    continue
                try:
                    signal_levels[decision.strategy_id] = _validated_signal_levels(decision)
                except Exception as exc:
                    signal_validation_errors[decision.strategy_id] = str(exc)

            new_signals = [
                decision for decision in engine_decisions
                if decision.status == "SIGNAL"
                and decision.strategy_id in signal_levels
                and _engine_state_key(symbol, decision.strategy_id) not in occupied_slots
            ]
            portfolio_risk_cap = (
                paper_equity_quote * RISK_FRACTION
                if paper_equity_quote is not None else 0.0
            )
            # Reserve a fixed half of the portfolio risk ceiling for each
            # supported symbol. Processing order therefore cannot let BTC
            # consume ETH's risk budget (or vice versa).
            total_risk_cap = portfolio_risk_cap / len(SYMBOLS)
            committed_risk = _committed_risk_quote(state, symbol=symbol)
            remaining_risk = max(0.0, total_risk_cap - committed_risk)
            per_signal_risk = (
                remaining_risk / len(new_signals)
                if new_signals and paper_equity_quote is not None else 0.0
            )
            allocations = {
                decision.strategy_id: per_signal_risk
                for decision in new_signals
                if per_signal_risk > 0
            }

            if paper_equity_quote is None:
                decision_status = "WAIT"
                reason = "نرخ معتبر و تازهٔ USDT/IRT در دسترس نیست؛ ورود جدید و فعال‌سازی سیگنال معلق متوقف شد."
            elif allocations:
                decision_status = "BUY"
                reason = "هر موتور واجد شرایط، بودجهٔ مستقل خود را از سقف ریسک مشترک دریافت می‌کند."
            elif new_signals:
                decision_status = "WAIT"
                reason = "سقف ریسک کل حساب آزمایشی قبلاً تخصیص یافته است؛ سیگنال‌ها ثبت شدند اما ورود جدید انجام نشد."
            elif selected:
                decision_status = "WAIT"
                reason = "هیچ موتورِ آزاد و واجد شرایط، سیگنال ورود معتبر نداد."
            else:
                decision_status = "WAIT"
                reason = snapshot["context"]["regime_reason"]

            if not selected:
                log_decisions = [{
                    "strategy_id": "regime_selector",
                    "strategy_version": "regime_selector_v1",
                    "status": "WAIT",
                    "reason": reason,
                    "evidence": {
                        "context": snapshot["context"],
                        "selected_engines": [],
                        "execution_mode": "paper_only",
                    },
                }]
            else:
                log_decisions = []
                for decision in engine_decisions:
                    slot_key = _engine_state_key(symbol, decision.strategy_id)
                    is_buy = decision.strategy_id in allocations
                    if paper_equity_quote is None:
                        event_reason = reason
                    elif decision.strategy_id in signal_validation_errors:
                        event_reason = (
                            "سیگنال این موتور به‌دلیل اشکال در سطوح ورود/حدضرر/هدف "
                            "رد شد؛ سایر موتورها مستقل ادامه می‌دهند: "
                            + signal_validation_errors[decision.strategy_id]
                        )
                    elif decision.status == "SIGNAL" and slot_key in occupied_slots:
                        event_reason = "برای همین موتور روی این نماد موقعیت یا ورود معلق خودش از قبل وجود دارد."
                    elif decision.status == "SIGNAL" and not is_buy:
                        event_reason = reason
                    else:
                        event_reason = decision.reason
                    engine_evidence = dict(decision.evidence)
                    own_snapshot = engine_snapshots.get(decision.strategy_id)
                    own_context = (
                        own_snapshot.get("context", snapshot["context"])
                        if isinstance(own_snapshot, dict) else snapshot["context"]
                    )
                    engine_evidence.update({
                        "engine_status": decision.status,
                        "engine_reason": decision.reason,
                        "engine_slot": slot_key,
                        "engine_risk_allocation_quote": allocations.get(decision.strategy_id, 0.0),
                        "portfolio_risk_cap_quote": portfolio_risk_cap,
                        "symbol_risk_cap_quote": total_risk_cap,
                        "symbol_risk_committed_before_new_signals_quote": committed_risk,
                        "context": own_context,
                        "selected_engines": list(selected),
                    })
                    log_decisions.append({
                        "strategy_id": decision.strategy_id,
                        "strategy_version": (
                            ENGINE_VERSION if decision.strategy_id == "trend_pullback"
                            else f"{decision.strategy_id}_v1"
                        ),
                        "status": "WAIT" if paper_equity_quote is None or (
                            decision.status == "SIGNAL" and (
                                not is_buy or decision.strategy_id in signal_validation_errors
                            )
                        ) else decision.status,
                        "reason": event_reason,
                        "evidence": engine_evidence,
                        "is_buy": is_buy,
                        "risk_quote": allocations.get(decision.strategy_id, 0.0),
                        "levels": signal_levels.get(decision.strategy_id),
                    })

            journal_errors: dict[str, str] = {}
            for item in log_decisions:
                strategy_id = item["strategy_id"]
                decision_id = _decision_id(symbol, latest_timestamp, strategy_id)
                if decision_id in known_decisions:
                    continue
                is_buy = bool(item.get("is_buy", False))
                try:
                    journal.append_decision(
                        decision_id=decision_id,
                        occurred_at=_iso(observed_at),
                        market_data_at=_iso(latest_timestamp + 300),
                        symbol=symbol,
                        action="BUY" if is_buy else "WAIT",
                        strategy=strategy_id,
                        strategy_version=item["strategy_version"],
                        reason=item["reason"],
                        evidence={
                            **item["evidence"],
                            "source": "Nobitex public OHLCV",
                            "resolutions": ["60", "15", "5"],
                            "spot_only": True,
                            "execution_mode": "paper_only",
                            "fees_rate": FEE_RATE,
                            "slippage_rate": SLIPPAGE_RATE,
                            "paper_equity_usdt": PAPER_EQUITY_USDT,
                            "usdt_irt_best_bid": usdt_irt_rate,
                            "paper_equity_quote_irt": paper_equity_quote,
                            "capital_rate_source": "Nobitex public USDTIRT orderbook best bid",
                        },
                        entry_price=item["levels"]["entry_reference"] if is_buy else None,
                        stop_loss=item["levels"]["stop_loss"] if is_buy else None,
                        take_profit=item["levels"]["take_profit"] if is_buy else None,
                        risk_quote=float(item["risk_quote"]) if is_buy else None,
                        timeframe="1h/15m/5m",
                        trade_id=f"paper-{decision_id}" if is_buy else None,
                    )
                except Exception as exc:
                    # A malformed engine record must not prevent another
                    # engine's valid decision from being journaled.
                    journal_errors[strategy_id] = f"{type(exc).__name__}: {exc}"
                    continue
                if is_buy:
                    slot_key = _engine_state_key(symbol, strategy_id)
                    state.data["pending"][slot_key] = {
                        "symbol": symbol,
                        "strategy": strategy_id,
                        "decision_id": decision_id,
                        "signal_timestamp": latest_timestamp,
                        "entry_reference": item["levels"]["entry_reference"],
                        "stop_loss": item["levels"]["stop_loss"],
                        "take_profit": item["levels"]["take_profit"],
                        "risk_quote": float(item["risk_quote"]),
                    }

            engine_report = [
                {
                    "strategy": item["strategy_id"],
                    "status": item["status"],
                    "journal_action": "BUY" if item.get("is_buy", False) else "WAIT",
                    "reason": item["reason"],
                }
                for item in log_decisions
            ]
            reports[symbol] = {
                "status": "ok",
                "latest_5m_timestamp": latest_timestamp,
                "trend_1h": snapshot["context"]["trend_1h"],
                "trend_15m": snapshot["context"]["trend_15m"],
                "regime": snapshot["regime"],
                "paper_equity_usdt": PAPER_EQUITY_USDT,
                "usdt_irt_best_bid": usdt_irt_rate,
                "paper_equity_quote_irt": paper_equity_quote,
                "capital_rate_error": capital_rate_error,
                "decision": decision_status,
                "selected_engines": list(selected),
                "engine_decisions": engine_report,
                "engine_journal_errors": journal_errors,
                "reason": reason,
                "position": position_status,
                "pending": pending_status,
                "open_position": any(
                    isinstance(record, dict) and _record_symbol(key, record) == symbol
                    for key, record in state.data["positions"].items()
                ),
                "pending_entry": any(
                    isinstance(record, dict) and _record_symbol(key, record) == symbol
                    for key, record in state.data["pending"].items()
                ),
                "engine_slots": {
                    key: {"position": key in state.data["positions"],
                          "pending": key in state.data["pending"]}
                    for key in set(state.data["positions"]) | set(state.data["pending"])
                    if isinstance(state.data["positions"].get(key, state.data["pending"].get(key)), dict)
                    and _record_symbol(key, state.data["positions"].get(key, state.data["pending"].get(key))) == symbol
                },
            }
        except (Exception) as exc:
            # A failed symbol observation is not converted into a fabricated WAIT candle.
            reports[symbol] = {
                "status": "data_or_evaluation_error",
                "error": f"{type(exc).__name__}: {exc}",
                "open_position": any(
                    isinstance(record, dict) and _record_symbol(key, record) == symbol
                    for key, record in state.data["positions"].items()
                ),
                "pending_entry": any(
                    isinstance(record, dict) and _record_symbol(key, record) == symbol
                    for key, record in state.data["pending"].items()
                ),
            }

    state.save()
    return {
        "observed_at": _iso(observed_at),
        "mode": "PAPER_ONLY_NO_EXCHANGE_ORDERS",
        "paper_equity_usdt": PAPER_EQUITY_USDT,
        "usdt_irt_best_bid": usdt_irt_rate,
        "paper_equity_quote_irt": paper_equity_quote,
        "capital_rate_error": capital_rate_error,
        "symbols": reports,
        "journal_summary": journal.summary(),
        "state_path": str(state.path),
        "journal_path": str(journal.path),
    }
