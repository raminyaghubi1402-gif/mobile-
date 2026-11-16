"""Link completed historical paper-backtest trades to their originating decisions."""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite

from .decision_journal import DecisionJournal, DecisionJournalError


class DecisionOutcomeLinkError(ValueError):
    """A closed trade could not be linked to its recorded decision."""


def _iso_utc(timestamp: int) -> str:
    if type(timestamp) is not int or timestamp < 0:
        raise DecisionOutcomeLinkError("trade timestamps must be non-negative integers")
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def append_backtest_outcomes(result, journal: DecisionJournal, *, run_id: str, symbol: str) -> int:
    """Append one cost-reconciled outcome per closed trade; safe to re-run."""
    if not isinstance(journal, DecisionJournal):
        raise DecisionOutcomeLinkError("journal must be a DecisionJournal")
    if not isinstance(run_id, str) or not run_id.strip():
        raise DecisionOutcomeLinkError("run_id is required")
    if symbol not in {"BTCIRT", "ETHIRT"}:
        raise DecisionOutcomeLinkError("only BTCIRT and ETHIRT are supported")
    trades = getattr(result, "trades", None)
    if not isinstance(trades, tuple):
        raise DecisionOutcomeLinkError("result must contain an immutable trades tuple")

    events = journal.events()
    decisions = {e.decision_id: e for e in events if e.event_type == "DECISION"}
    outcomes = {e.decision_id: e for e in events if e.event_type == "OUTCOME"}
    appended = 0
    for trade in trades:
        if trade.direction not in {"BUY", "SELL"}:
            raise DecisionOutcomeLinkError("unsupported trade direction")
        raw_costs = (trade.fees, trade.slippage_quote, trade.net_pnl)
        if any(
            isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value)
            for value in raw_costs
        ):
            raise DecisionOutcomeLinkError("trade costs and net PnL must be finite numeric values")
        if trade.fees < 0 or trade.slippage_quote < 0:
            raise DecisionOutcomeLinkError("trade fees and slippage must be non-negative")
        decision_id = f"{run_id}:{symbol}:{trade.entry_timestamp}"
        decision = decisions.get(decision_id)
        if decision is None:
            raise DecisionOutcomeLinkError(
                f"no originating decision for closed trade at {trade.entry_timestamp}"
            )
        if decision.payload.get("action") != trade.direction:
            raise DecisionOutcomeLinkError("trade direction does not match its decision")
        trade_id = decision.payload.get("trade_id")
        if not isinstance(trade_id, str) or not trade_id:
            raise DecisionOutcomeLinkError("originating decision has no trade_id")
        entry_at = _iso_utc(trade.entry_timestamp)
        exit_at = _iso_utc(trade.exit_timestamp)
        fees = float(trade.fees)
        slippage = float(trade.slippage_quote)
        net = float(trade.net_pnl)
        gross_before_costs = net + fees + slippage
        payload = {
            "trade_id": trade_id, "side": trade.direction,
            "entry_at": entry_at, "exit_at": exit_at,
            "gross_pnl_quote": gross_before_costs,
            "fees_quote": fees, "slippage_quote": slippage,
            "net_pnl_quote": net, "exit_reason": trade.exit_reason,
            "review_status": "needs_human_review",
        }
        previous = outcomes.get(decision_id)
        if previous is not None:
            if previous.payload != payload:
                raise DecisionOutcomeLinkError("existing outcome differs from this backtest result")
            continue
        try:
            journal.append_outcome(
                decision_id=decision_id, occurred_at=exit_at,
                trade_id=trade_id, side=trade.direction,
                entry_at=entry_at, exit_at=exit_at,
                gross_pnl_quote=gross_before_costs, fees_quote=fees,
                slippage_quote=slippage, net_pnl_quote=net,
                exit_reason=trade.exit_reason,
            )
        except DecisionJournalError as exc:
            raise DecisionOutcomeLinkError("trade outcome could not be appended") from exc
        appended += 1
    return appended
