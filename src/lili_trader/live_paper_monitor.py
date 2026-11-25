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
from .strategy_engines import build_default_strategy_registry
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
    """Atomic local state for pending entries and one paper position per symbol."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.data: dict[str, Any] = {"schema_version": 1, "pending": {}, "positions": {}}
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise LivePaperMonitorError("paper state is unreadable; refusing to reset it") from exc
            if (not isinstance(loaded, dict) or loaded.get("schema_version") != 1
                    or not isinstance(loaded.get("pending"), dict)
                    or not isinstance(loaded.get("positions"), dict)):
                raise LivePaperMonitorError("paper state schema is invalid; refusing to reset it")
            self.data = loaded

    def save(self) -> None:
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
) -> str:
    position = state.data["positions"].get(symbol)
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
            del state.data["positions"][symbol]
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

    state.data["positions"][symbol] = position
    if stop_updates:
        return f"position_open_trailing_stop_raised_{stop_updates}_times_to_{float(position['stop_loss']):.2f}"
    return "position_open"


def _reserved_quote(
    state: PaperState, *, exclude_pending_symbol: str, equity_quote: float
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
    for symbol, position in state.data["positions"].items():
        if symbol not in SYMBOLS or not isinstance(position, dict):
            raise LivePaperMonitorError("paper position state is invalid")
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

    for symbol, pending in state.data["pending"].items():
        if symbol == exclude_pending_symbol:
            continue
        if symbol not in SYMBOLS or not isinstance(pending, dict):
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


def _activate_pending(
    symbol: str,
    candles: list[NobitexCandle],
    state: PaperState,
    *,
    equity_quote: float,
) -> str:
    if not isfinite(equity_quote) or equity_quote <= 0:
        raise LivePaperMonitorError("paper equity quote must be finite and positive")
    pending = state.data["pending"].get(symbol)
    if not pending or symbol in state.data["positions"]:
        return "no_pending_entry"
    expected = int(pending["signal_timestamp"]) + 300
    next_candle = next((c for c in candles if c.timestamp >= expected), None)
    if next_candle is None:
        return "pending_next_5m_candle"
    if next_candle.timestamp != expected:
        del state.data["pending"][symbol]
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
        del state.data["pending"][symbol]
        return "pending_entry_cancelled_gap_or_invalid_risk"
    risk_budget = float(pending["risk_quote"])
    if not isfinite(risk_budget) or risk_budget <= 0:
        raise LivePaperMonitorError("pending entry risk budget is invalid")
    available_cash = max(
        0.0, equity_quote - _reserved_quote(
            state, exclude_pending_symbol=symbol, equity_quote=equity_quote
        )
    )
    quantity_by_risk = risk_budget / unit_risk
    quantity_by_cash = available_cash / (entry_fill * (1 + FEE_RATE))
    quantity = min(quantity_by_risk, quantity_by_cash)
    if quantity <= 0:
        del state.data["pending"][symbol]
        return "pending_entry_cancelled_zero_quantity"
    entry_notional = entry_fill * quantity
    if entry_notional + 1e-9 < NOBITEX_MINIMUM_SPOT_ORDER_IRT:
        del state.data["pending"][symbol]
        return (
            "pending_entry_cancelled_below_minimum_order_"
            f"{NOBITEX_MINIMUM_SPOT_ORDER_IRT}"
        )
    state.data["positions"][symbol] = {
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
    del state.data["pending"][symbol]
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
            position_status = _manage_position(
                symbol, validated["5m"], state, journal, now=observed_at
            )
            pending_status = (
                _activate_pending(
                    symbol, validated["5m"], state, equity_quote=paper_equity_quote
                )
                if paper_equity_quote is not None
                else "pending_entry_paused_usdt_irt_rate_unavailable"
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
            regime = apply_primary_trend(
                regime_15m,
                snapshot["context"]["trend_1h"],
                snapshot["context"]["trend_15m"],
            )
            snapshot["context"]["primary_trend"] = regime.trend
            snapshot["context"]["primary_trend_timeframe"] = (
                "15m" if snapshot["context"]["trend_1h"] == "range" else "1h"
            )
            candidates = select_strategy_candidates(regime, allow_short=False)
            eligible_ids = {candidate.name for candidate in candidates if candidate.eligible}
            selected = tuple(
                engine_id for engine_id in registry.registered_ids
                if regime.trend == "up" and regime.volatility in {"low", "normal"}
                and engine_id in eligible_ids
            )

            occupied = symbol in state.data["positions"] or symbol in state.data["pending"]
            occupied_reason = "برای این نماد معاملهٔ آزمایشی باز یا سیگنال در انتظار ورود وجود دارد."
            engine_decisions = [
                registry.evaluate_selected(engine_snapshots[engine_id], (engine_id,))[0]
                for engine_id in selected
            ]
            signals = [decision for decision in engine_decisions if decision.status == "SIGNAL"]
            conflict = len(signals) > 1
            executable = (
                signals[0]
                if len(signals) == 1 and not occupied and paper_equity_quote is not None
                else None
            )

            if paper_equity_quote is None:
                decision_status = "WAIT"
                reason = "نرخ معتبر و تازهٔ USDT/IRT در دسترس نیست؛ ورود جدید و فعال‌سازی سیگنال معلق متوقف شد."
            elif occupied:
                decision_status = "WAIT"
                reason = occupied_reason
            elif conflict:
                decision_status = "WAIT"
                reason = (
                    "چند موتور مستقل هم‌زمان سیگنال دادند؛ برای جلوگیری از ثبت چند ورود "
                    "روی یک نماد، این کندل بدون ورود آزمایشی ثبت شد."
                )
            elif executable is not None:
                decision_status = "BUY"
                reason = executable.reason
            elif selected:
                decision_status = "WAIT"
                reason = "هیچ‌یک از موتورهای مستقل در این کندل سیگنال ورود معتبر ندادند."
            else:
                decision_status = "WAIT"
                reason = snapshot["context"]["regime_reason"]

            latest_timestamp = int(validated["5m"][-1].timestamp)
            known_decisions = {
                event.decision_id for event in journal.events()
                if event.event_type == "DECISION"
            }
            risk_budget = (
                paper_equity_quote * RISK_FRACTION
                if paper_equity_quote is not None else 0.0
            )

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
                    is_buy = decision is executable
                    if paper_equity_quote is None:
                        event_reason = reason
                    elif occupied:
                        event_reason = occupied_reason
                    elif conflict:
                        event_reason = reason
                    else:
                        event_reason = decision.reason
                    engine_evidence = dict(decision.evidence)
                    engine_evidence.update({
                        "engine_status": decision.status,
                        "engine_reason": decision.reason,
                        "context": engine_snapshots[decision.strategy_id].get(
                            "context", snapshot["context"]
                        ),
                        "selected_engines": list(selected),
                    })
                    log_decisions.append({
                        "strategy_id": decision.strategy_id,
                        "strategy_version": (
                            ENGINE_VERSION if decision.strategy_id == "trend_pullback"
                            else f"{decision.strategy_id}_v1"
                        ),
                        "status": "WAIT" if paper_equity_quote is None else decision.status,
                        "reason": event_reason,
                        "evidence": engine_evidence,
                        "is_buy": is_buy,
                    })

            for item in log_decisions:
                strategy_id = item["strategy_id"]
                decision_id = _decision_id(symbol, latest_timestamp, strategy_id)
                if decision_id in known_decisions:
                    continue
                is_buy = bool(item.get("is_buy", False))
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
                    entry_price=float(item["evidence"]["entry_reference"]) if is_buy else None,
                    stop_loss=float(item["evidence"]["stop_loss"]) if is_buy else None,
                    take_profit=float(item["evidence"]["take_profit"]) if is_buy else None,
                    risk_quote=risk_budget if is_buy else None,
                    timeframe="1h/15m/5m",
                    trade_id=f"paper-{decision_id}" if is_buy else None,
                )
                if is_buy:
                    state.data["pending"][symbol] = {
                        "decision_id": decision_id,
                        "signal_timestamp": latest_timestamp,
                        "entry_reference": float(item["evidence"]["entry_reference"]),
                        "stop_loss": float(item["evidence"]["stop_loss"]),
                        "take_profit": float(item["evidence"]["take_profit"]),
                        "risk_quote": risk_budget,
                        "strategy": strategy_id,
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
                "reason": reason,
                "position": position_status,
                "pending": pending_status,
                "open_position": symbol in state.data["positions"],
                "pending_entry": symbol in state.data["pending"],
            }
        except (Exception) as exc:
            # A failed symbol observation is not converted into a fabricated WAIT candle.
            reports[symbol] = {
                "status": "data_or_evaluation_error",
                "error": f"{type(exc).__name__}: {exc}",
                "open_position": symbol in state.data["positions"],
                "pending_entry": symbol in state.data["pending"],
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
