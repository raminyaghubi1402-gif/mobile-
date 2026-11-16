"""Best-effort, scheduled forward-paper monitor for Nobitex BTC/ETH spot pairs.

This module is read-only against Nobitex. It never places exchange orders.
The strategy is replayed from a compact persisted segment so an open paper
position can be reconstructed after a runner restart.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from math import isfinite
from pathlib import Path
from typing import Any

from .decision_journal import DecisionJournal
from .decision_outcome import append_backtest_outcomes
from .history_store import merge_candle_history
from .nobitex_data import NobitexAdapter
from .nobitex_limits import (
    NOBITEX_BASE_SPOT_FEE_RATE_IRT,
    NOBITEX_MINIMUM_SPOT_ORDER_IRT,
)
from .price_action_backtest import run_three_timeframe_backtest
from .timeframe_resample import extend_fifteen_with_five


SYMBOLS = ("BTCIRT", "ETHIRT")
RUN_ID = "forward-paper-v1"
TOTAL_CAPITAL_USD = 10.0
POLL_INTERVAL_SECONDS = 300
MAX_FIVE_MINUTE_LAG_SECONDS = 180
FIVE_MINUTE_COUNT = 2000
FIFTEEN_MINUTE_COUNT = 500
HOURLY_COUNT = 500
MAX_HISTORY_ROWS = 20_000
FEE_RATE = NOBITEX_BASE_SPOT_FEE_RATE_IRT
SLIPPAGE_RATE = 0.001


class ForwardPaperError(RuntimeError):
    """Forward-paper cycle stopped safely because its inputs/state are unsafe."""


def _iso_now(timestamp: int | None = None) -> str:
    return datetime.fromtimestamp(
        time.time() if timestamp is None else timestamp, timezone.utc
    ).isoformat()


def _parse_aware_timestamp(value: str, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ForwardPaperError(f"{field} is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ForwardPaperError(f"{field} must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ForwardPaperError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _validate_seed_rate(rate: float | None, source: str | None,
                        observed_at: str | None, now_ts: int) -> tuple[float, str, str]:
    if isinstance(rate, bool) or not isinstance(rate, (int, float)):
        raise ForwardPaperError("initial USD/IRT rate must be a positive number")
    if not isfinite(rate) or rate <= 0:
        raise ForwardPaperError("initial USD/IRT rate must be a positive finite number")
    if not isinstance(source, str) or not source.strip():
        raise ForwardPaperError("initial USD/IRT rate source is required")
    observed = _parse_aware_timestamp(observed_at, "initial USD/IRT rate timestamp")
    age = now_ts - observed.timestamp()
    if age < -60 or age > 900:
        raise ForwardPaperError("initial USD/IRT rate must be no more than 15 minutes old")
    return float(rate), source.strip(), observed.isoformat()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _load_state(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ForwardPaperError("paper account state is unreadable") from exc
    if not isinstance(state, dict) or state.get("schema_version") != 1:
        raise ForwardPaperError("unsupported paper account state schema")
    if state.get("symbols") is None or set(state["symbols"]) != set(SYMBOLS):
        raise ForwardPaperError("paper account state must contain BTCIRT and ETHIRT")
    if state.get("run_id") != RUN_ID:
        raise ForwardPaperError("paper account run id mismatch")
    for symbol in SYMBOLS:
        item = state["symbols"].get(symbol)
        if not isinstance(item, dict):
            raise ForwardPaperError(f"invalid paper account state for {symbol}")
        for key in ("replay_start_timestamp", "last_processed_signal_close"):
            if type(item.get(key)) is not int or item[key] <= 0:
                raise ForwardPaperError(f"invalid {symbol} state field: {key}")
        for key in ("replay_start_equity_irt", "last_equity_irt"):
            value = item.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value <= 0:
                raise ForwardPaperError(f"invalid {symbol} state field: {key}")
    return state


def _initial_state(rate: float, source: str, observed_at: str,
                   latest_five: dict[str, int], now_ts: int) -> dict[str, Any]:
    per_symbol_usd = TOTAL_CAPITAL_USD / len(SYMBOLS)
    symbols: dict[str, Any] = {}
    for symbol in SYMBOLS:
        equity = per_symbol_usd * rate
        # The most recent 5m candle is the current execution candle. Its open
        # is not used retrospectively; the next signal is eligible next cycle.
        symbols[symbol] = {
            "starting_capital_usd": per_symbol_usd,
            "starting_equity_irt": equity,
            "replay_start_timestamp": latest_five[symbol] + POLL_INTERVAL_SECONDS,
            "replay_start_equity_irt": equity,
            "last_processed_signal_close": latest_five[symbol],
            "last_equity_irt": equity,
            "last_marked_equity_irt": equity,
            "open_trade_id": None,
            "last_market_data_at": _iso_now(latest_five[symbol]),
            "last_cycle_at": _iso_now(now_ts),
            "new_decisions_last_cycle": 0,
            "closed_trades_last_cycle": 0,
        }
    return {
        "schema_version": 1,
        "run_id": RUN_ID,
        "mode": "PAPER_ONLY_NO_EXCHANGE_ORDERS",
        "created_at": _iso_now(now_ts),
        "initial_capital_usd": TOTAL_CAPITAL_USD,
        "usd_irt_rate": rate,
        "usd_irt_rate_source": source,
        "usd_irt_rate_observed_at": observed_at,
        "capital_allocation": "5 USD per symbol; total 10 USD",
        "fee_rate": FEE_RATE,
        "slippage_rate": SLIPPAGE_RATE,
        "minimum_order_quote_irt": NOBITEX_MINIMUM_SPOT_ORDER_IRT,
        "symbols": symbols,
        "last_cycle_at": _iso_now(now_ts),
    }


def _closed_and_current(candles, resolution_seconds: int, now_ts: int):
    # Keep the current in-progress 5m candle as the execution/position-monitor
    # bar. Higher timeframes are safely consumed only after their close by the
    # backtest engine's own close-time cursors.
    return [
        candle for candle in candles
        if candle.timestamp <= now_ts
        and candle.timestamp + resolution_seconds > now_ts - resolution_seconds
    ] if resolution_seconds == 300 else [
        candle for candle in candles if candle.timestamp <= now_ts
    ]


def _timestamp_from_iso(value: str) -> int:
    return int(_parse_aware_timestamp(value, "decision timestamp").timestamp())


def run_forward_cycle(
    state_dir: str | Path,
    *,
    usd_irt_rate: float | None = None,
    usd_irt_rate_source: str | None = None,
    usd_irt_rate_at: str | None = None,
    adapter: NobitexAdapter | None = None,
    now_ts: int | None = None,
    max_lag_seconds: int = MAX_FIVE_MINUTE_LAG_SECONDS,
) -> dict[str, Any]:
    """Fetch, validate, replay and journal one scheduled paper cycle.

    On first run, a fresh user-supplied USD/IRT seed rate is required. Later
    cycles retain that fixed conversion basis; it is not a live FX mark.
    """
    if type(max_lag_seconds) is not int or not 30 <= max_lag_seconds <= 240:
        raise ForwardPaperError("max_lag_seconds must be between 30 and 240")
    now = int(time.time()) if now_ts is None else now_ts
    if type(now) is not int or now <= 0:
        raise ForwardPaperError("now_ts must be a positive Unix timestamp")
    adapter = adapter or NobitexAdapter()
    directory = Path(state_dir)
    state_path = directory / "account.json"
    journal_path = directory / "decisions.jsonl"
    state = _load_state(state_path)
    journal = DecisionJournal(journal_path)

    market: dict[str, tuple[list, list, list]] = {}
    latest_five: dict[str, int] = {}
    for symbol in SYMBOLS:
        try:
            five = adapter.fetch_history(symbol, "5", FIVE_MINUTE_COUNT)
            native_fifteen = adapter.fetch_history(symbol, "15", FIFTEEN_MINUTE_COUNT)
            hourly = adapter.fetch_history(symbol, "60", HOURLY_COUNT)
        except Exception as exc:
            raise ForwardPaperError(f"Nobitex data fetch failed for {symbol}: {type(exc).__name__}") from exc
        five = sorted((c for c in five if c.timestamp <= now), key=lambda c: c.timestamp)
        native_fifteen = sorted((c for c in native_fifteen if c.timestamp <= now), key=lambda c: c.timestamp)
        hourly = sorted((c for c in hourly if c.timestamp <= now), key=lambda c: c.timestamp)
        if len(five) < 100 or len(hourly) < 30:
            raise ForwardPaperError(f"insufficient candle history for {symbol}")
        current_bucket = now - (now % 300)
        current_five = five[-1]
        if current_five.timestamp != current_bucket:
            raise ForwardPaperError(f"{symbol} latest 5m candle is not the current execution candle")
        lag = now - current_five.timestamp
        if lag > max_lag_seconds:
            raise ForwardPaperError(f"{symbol} current 5m candle is stale ({lag}s)")
        fifteen = extend_fifteen_with_five(native_fifteen, five)
        if len(fifteen) < 30:
            raise ForwardPaperError(f"insufficient 15m history for {symbol}")
        market[symbol] = (hourly, fifteen, five)
        latest_five[symbol] = current_five.timestamp

    if state is None:
        if usd_irt_rate is None:
            return {
                "status": "awaiting_initialization",
                "message": "Paper account not initialized. Manually dispatch the workflow with a fresh USD/IRT rate, source and timestamp.",
            }
        rate, source, observed = _validate_seed_rate(
            usd_irt_rate, usd_irt_rate_source, usd_irt_rate_at, now
        )
        state = _initial_state(rate, source, observed, latest_five, now)
        _atomic_json(state_path, state)
        return {
            "status": "initialized",
            "message": "10 USD paper capital initialized as 5 USD per symbol. No historical signal was traded on initialization.",
            "initial_capital_usd": TOTAL_CAPITAL_USD,
            "usd_irt_rate": rate,
            "symbols": list(SYMBOLS),
        }

    journal_events = journal.events()
    existing_decision_ids = {
        event.decision_id for event in journal_events if event.event_type == "DECISION"
    }
    results: dict[str, Any] = {}
    for symbol in SYMBOLS:
        hourly, fifteen, five = market[symbol]
        account = state["symbols"][symbol]
        latest_signal_close = five[-1].timestamp

        replay_start = account["replay_start_timestamp"]
        replay_equity = float(account["replay_start_equity_irt"])
        previous_last_processed = account["last_processed_signal_close"]
        open_trade_id = account.get("open_trade_id")

        if replay_start < five[0].timestamp:
            if open_trade_id:
                raise ForwardPaperError(
                    f"{symbol} open paper trade is older than retained 5m history; refusing to forget the position"
                )
            if previous_last_processed < five[0].timestamp:
                raise ForwardPaperError(
                    f"{symbol} missed candles older than retained history; refusing to skip unprocessed data"
                )
            replay_start = previous_last_processed + 300
            replay_equity = float(account["last_equity_irt"])

        # If a scheduled run was delayed, only the latest signal may open a
        # new position. Missed intermediate signals are never backfilled.
        eligible_entries = {latest_signal_close}
        if open_trade_id:
            eligible_entries.add(replay_start)

        new_decisions = 0

        def record_latest_decision(event: dict) -> None:
            nonlocal new_decisions
            event_timestamp = _timestamp_from_iso(event["occurred_at"])
            if event_timestamp != latest_signal_close:
                return
            if event_timestamp <= previous_last_processed:
                return
            if event["decision_id"] in existing_decision_ids:
                return
            journal.append_decision(
                decision_id=event["decision_id"],
                occurred_at=event["occurred_at"],
                market_data_at=event["market_data_at"],
                symbol=event["symbol"],
                action=event["action"],
                strategy=event["strategy"],
                strategy_version=event["strategy_version"],
                reason=event["reason"],
                evidence={
                    **event["evidence"],
                    "forward_paper": True,
                    "execution_mode": "PAPER_ONLY_NO_EXCHANGE_ORDERS",
                    "capital_usd_allocated": account["starting_capital_usd"],
                    "capital_seed_usd_irt_rate": state["usd_irt_rate"],
                    "capital_seed_rate_source": state["usd_irt_rate_source"],
                    "capital_seed_rate_at": state["usd_irt_rate_observed_at"],
                },
                entry_price=event["entry_price"],
                stop_loss=event["stop_loss"],
                take_profit=event["take_profit"],
                risk_quote=event["risk_quote"],
                timeframe=event["timeframe"],
                trade_id=event["trade_id"],
            )
            existing_decision_ids.add(event["decision_id"])
            new_decisions += 1

        try:
            result = run_three_timeframe_backtest(
                hourly, fifteen, five,
                starting_cash=replay_equity,
                fee_rate=FEE_RATE,
                slippage_rate=SLIPPAGE_RATE,
                decision_callback=record_latest_decision,
                decision_run_id=RUN_ID,
                symbol=symbol,
                allow_short=False,
                minimum_order_quote=NOBITEX_MINIMUM_SPOT_ORDER_IRT,
                trade_start_timestamp=replay_start,
                allowed_entry_timestamps=eligible_entries,
            )
            closed_linked = append_backtest_outcomes(
                result, journal, run_id=RUN_ID, symbol=symbol
            )
        except Exception as exc:
            raise ForwardPaperError(f"{symbol} forward-paper evaluation failed: {type(exc).__name__}") from exc

        open_trade = (
            result.trades[-1]
            if result.trades and result.trades[-1].exit_reason == "end_of_data"
            else None
        )
        if open_trade is not None:
            closed_before_open = result.trades[:-1]
            base_equity = replay_equity + sum(trade.net_pnl for trade in closed_before_open)
            account["replay_start_timestamp"] = open_trade.entry_timestamp
            account["replay_start_equity_irt"] = base_equity
            account["open_trade_id"] = f"{RUN_ID}:{symbol}:{open_trade.entry_timestamp}"
            account["last_equity_irt"] = base_equity
            account["last_marked_equity_irt"] = result.ending_equity
        else:
            account["replay_start_timestamp"] = latest_signal_close + 300
            account["replay_start_equity_irt"] = result.ending_equity
            account["open_trade_id"] = None
            account["last_equity_irt"] = result.ending_equity
            account["last_marked_equity_irt"] = result.ending_equity

        account["last_processed_signal_close"] = latest_signal_close
        account["last_market_data_at"] = _iso_now(latest_signal_close)
        account["last_cycle_at"] = _iso_now(now)
        account["new_decisions_last_cycle"] = new_decisions
        account["closed_trades_last_cycle"] = closed_linked
        account["last_gate_counts"] = dict(result.gate_counts)
        account["last_trade_count_in_replay"] = result.trade_count
        account["last_max_drawdown_pct_in_replay"] = result.max_drawdown_pct
        account["last_cycle_status"] = "ok"
        results[symbol] = {
            "new_decisions": new_decisions,
            "closed_trades_linked": closed_linked,
            "open_position": open_trade is not None,
            "realized_equity_irt": account["last_equity_irt"],
            "marked_equity_irt": account["last_marked_equity_irt"],
            "replay_trade_count": result.trade_count,
            "max_drawdown_pct_in_replay": result.max_drawdown_pct,
        }

    state["last_cycle_at"] = _iso_now(now)
    state["symbols"] = state["symbols"]
    _atomic_json(state_path, state)
    return {
        "status": "ok",
        "mode": "PAPER_ONLY_NO_EXCHANGE_ORDERS",
        "initial_capital_usd": state["initial_capital_usd"],
        "usd_irt_seed_rate": state["usd_irt_rate"],
        "symbols": results,
        "journal_summary": journal.summary(),
    }
