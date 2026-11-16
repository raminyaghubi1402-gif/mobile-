"""Append-only decision and post-trade review journal for paper trading.

Local-only: no exchange, broker, wallet, or network access.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from pathlib import Path
from typing import Any


class DecisionJournalError(ValueError):
    """Invalid decision/review event or a corrupted journal."""


_ALLOWED_SYMBOLS = {"BTCIRT", "ETHIRT"}
_ALLOWED_ACTIONS = {"BUY", "SELL", "WAIT"}
_ALLOWED_OUTCOMES = {"GOOD", "BAD", "NEUTRAL", "EXECUTION_ERROR"}


def _timestamp(value: Any) -> None:
    if not isinstance(value, str) or not value.strip():
        raise DecisionJournalError("occurred_at must be a timezone-aware ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DecisionJournalError("occurred_at must be a timezone-aware ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DecisionJournalError("occurred_at must include a timezone")


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DecisionJournalError(f"{field} is required")
    return value.strip()


def _positive(value: Any, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DecisionJournalError(f"{field} must be finite and positive")
    if not isfinite(value) or value <= 0:
        raise DecisionJournalError(f"{field} must be finite and positive")


def _canonical(value: dict[str, Any]) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise DecisionJournalError("event payload must contain finite JSON values only") from exc


@dataclass(frozen=True)
class DecisionEvent:
    sequence: int
    event_id: str
    event_type: str
    occurred_at: str
    decision_id: str
    payload: dict[str, Any]
    previous_hash: str
    event_hash: str


class DecisionJournal:
    """Single-writer JSONL journal with a verifiable hash chain.

    Hash chaining detects accidental edits/truncation/reordering. It is not a
    digital signature and cannot prevent a person who can rewrite the file
    from recomputing the entire chain.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @staticmethod
    def _digest(body: dict[str, Any]) -> str:
        return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()

    def events(self) -> tuple[DecisionEvent, ...]:
        if not self.path.exists():
            return ()
        result: list[DecisionEvent] = []
        previous_hash = "GENESIS"
        decision_ids: set[str] = set()
        reviewed_ids: set[str] = set()
        try:
            with self.path.open("r", encoding="utf-8") as stream:
                for line_number, line in enumerate(stream, 1):
                    if not line.strip():
                        continue
                    try:
                        raw = json.loads(line)
                        event = DecisionEvent(**raw)
                    except (json.JSONDecodeError, TypeError) as exc:
                        raise DecisionJournalError(f"invalid event at line {line_number}") from exc
                    if event.sequence != len(result) + 1:
                        raise DecisionJournalError(f"sequence mismatch at line {line_number}")
                    if event.previous_hash != previous_hash:
                        raise DecisionJournalError(f"hash-chain link mismatch at line {line_number}")
                    body = {
                        "sequence": event.sequence,
                        "event_id": event.event_id,
                        "event_type": event.event_type,
                        "occurred_at": event.occurred_at,
                        "decision_id": event.decision_id,
                        "payload": event.payload,
                        "previous_hash": event.previous_hash,
                    }
                    if self._digest(body) != event.event_hash:
                        raise DecisionJournalError(f"event hash mismatch at line {line_number}")
                    self._validate_event(event)
                    if event.event_type == "DECISION":
                        if event.decision_id in decision_ids:
                            raise DecisionJournalError("duplicate decision_id")
                        decision_ids.add(event.decision_id)
                    else:
                        if event.decision_id not in decision_ids:
                            raise DecisionJournalError("review references an unknown decision_id")
                        if event.decision_id in reviewed_ids:
                            raise DecisionJournalError("decision has more than one review")
                        reviewed_ids.add(event.decision_id)
                    result.append(event)
                    previous_hash = event.event_hash
        except OSError as exc:
            raise DecisionJournalError("decision journal cannot be read") from exc
        return tuple(result)

    @staticmethod
    def _validate_event(event: DecisionEvent) -> None:
        if event.event_type not in {"DECISION", "REVIEW"}:
            raise DecisionJournalError("event_type must be DECISION or REVIEW")
        _text(event.event_id, "event_id")
        _text(event.decision_id, "decision_id")
        _timestamp(event.occurred_at)
        if not isinstance(event.payload, dict):
            raise DecisionJournalError("payload must be an object")
        if event.event_type == "DECISION":
            p = event.payload
            if p.get("symbol") not in _ALLOWED_SYMBOLS:
                raise DecisionJournalError("only BTCIRT and ETHIRT are supported")
            if p.get("action") not in _ALLOWED_ACTIONS:
                raise DecisionJournalError("action must be BUY, SELL or WAIT")
            _text(p.get("strategy"), "strategy")
            _text(p.get("strategy_version"), "strategy_version")
            _text(p.get("reason"), "reason")
            _timestamp(p.get("market_data_at"))
            if not isinstance(p.get("evidence"), dict):
                raise DecisionJournalError("evidence must be an object")
            if p["action"] in {"BUY", "SELL"}:
                for key in ("entry_price", "stop_loss", "take_profit", "risk_quote"):
                    _positive(p.get(key), key)
        else:
            p = event.payload
            if p.get("outcome") not in _ALLOWED_OUTCOMES:
                raise DecisionJournalError("invalid review outcome")
            _text(p.get("trade_id"), "trade_id")
            tags = p.get("failure_tags")
            if not isinstance(tags, list) or any(not isinstance(tag, str) or not tag.strip() for tag in tags):
                raise DecisionJournalError("failure_tags must be a list of non-empty strings")
            _text(p.get("root_cause"), "root_cause")
            _text(p.get("corrective_action"), "corrective_action")

    def _append(self, event_type: str, occurred_at: str, decision_id: str,
                payload: dict[str, Any]) -> DecisionEvent:
        _timestamp(occurred_at)
        existing = self.events()
        event_id = f"{event_type.lower()}-{decision_id}"
        if any(item.event_id == event_id for item in existing):
            raise DecisionJournalError("event_id has already been used")
        if event_type == "DECISION" and any(item.decision_id == decision_id for item in existing):
            raise DecisionJournalError("decision_id has already been used")
        if event_type == "REVIEW":
            if not any(item.event_type == "DECISION" and item.decision_id == decision_id for item in existing):
                raise DecisionJournalError("cannot review an unknown decision")
            if any(item.event_type == "REVIEW" and item.decision_id == decision_id for item in existing):
                raise DecisionJournalError("decision has already been reviewed")
        sequence = len(existing) + 1
        previous_hash = existing[-1].event_hash if existing else "GENESIS"
        body = {
            "sequence": sequence,
            "event_id": event_id,
            "event_type": event_type,
            "occurred_at": occurred_at,
            "decision_id": decision_id,
            "payload": payload,
            "previous_hash": previous_hash,
        }
        event = DecisionEvent(**body, event_hash=self._digest(body))
        self._validate_event(event)
        serialized = _canonical({
            **body,
            "event_hash": event.event_hash,
        })
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="") as stream:
                stream.write(serialized + "\n")
                stream.flush()
        except OSError as exc:
            raise DecisionJournalError("event could not be appended") from exc
        return event

    def append_decision(
        self, *, decision_id: str, occurred_at: str, market_data_at: str,
        symbol: str, action: str, strategy: str, strategy_version: str,
        reason: str, evidence: dict[str, Any],
        entry_price: float | None = None, stop_loss: float | None = None,
        take_profit: float | None = None, risk_quote: float | None = None,
        timeframe: str = "1h/15m/5m", trade_id: str | None = None,
    ) -> DecisionEvent:
        """Record BUY/SELL/WAIT before any simulated execution."""
        payload = {
            "symbol": symbol, "action": action, "strategy": strategy,
            "strategy_version": strategy_version, "reason": reason,
            "market_data_at": market_data_at, "timeframe": timeframe,
            "evidence": evidence, "entry_price": entry_price,
            "stop_loss": stop_loss, "take_profit": take_profit,
            "risk_quote": risk_quote, "trade_id": trade_id,
            "execution_mode": "PAPER_ONLY_NO_EXCHANGE_ORDER",
        }
        return self._append("DECISION", occurred_at, decision_id, payload)

    def append_review(
        self, *, decision_id: str, occurred_at: str, trade_id: str,
        outcome: str, failure_tags: list[str], root_cause: str,
        corrective_action: str, notes: str = "",
    ) -> DecisionEvent:
        """Append one human-auditable post-trade review; never edits strategy."""
        payload = {
            "trade_id": trade_id, "outcome": outcome,
            "failure_tags": failure_tags, "root_cause": root_cause,
            "corrective_action": corrective_action, "notes": notes,
        }
        return self._append("REVIEW", occurred_at, decision_id, payload)

    def summary(self) -> dict[str, Any]:
        events = self.events()
        decisions = [e for e in events if e.event_type == "DECISION"]
        reviews = [e for e in events if e.event_type == "REVIEW"]
        tags = Counter(tag for e in reviews for tag in e.payload["failure_tags"])
        return {
            "decisions": len(decisions),
            "buy": sum(e.payload["action"] == "BUY" for e in decisions),
            "sell": sum(e.payload["action"] == "SELL" for e in decisions),
            "wait": sum(e.payload["action"] == "WAIT" for e in decisions),
            "reviews": len(reviews),
            "bad_reviews": sum(e.payload["outcome"] in {"BAD", "EXECUTION_ERROR"} for e in reviews),
            "failure_tags": dict(sorted(tags.items())),
            "last_hash": events[-1].event_hash if events else None,
        }
