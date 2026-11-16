"""Append-only, local paper-trade journal. No network or broker access."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from math import isclose, isfinite
from pathlib import Path
from typing import Any


class JournalError(ValueError):
    """Invalid journal record or corrupted local journal."""


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def _timestamp(value: str, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise JournalError(f"{field} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise JournalError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise JournalError(f"{field} must include a timezone")
    return parsed


@dataclass(frozen=True)
class TradeRecord:
    trade_id: str
    symbol: str
    strategy: str
    side: str
    status: str
    entry_time: str
    entry_price: float
    quantity: float
    initial_risk_quote: float
    entry_reason: str
    exit_time: str | None = None
    exit_price: float | None = None
    gross_pnl_quote: float | None = None
    fees_quote: float = 0.0
    slippage_quote: float = 0.0
    net_pnl_quote: float | None = None
    exit_reason: str | None = None

    def validate(self) -> None:
        for field in ("trade_id", "symbol", "strategy", "entry_reason"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise JournalError(f"{field} is required")
        if self.side not in ("long", "short"):
            raise JournalError("side must be long or short")
        if self.status not in ("open", "closed"):
            raise JournalError("status must be open or closed")
        entry = _timestamp(self.entry_time, "entry_time")
        required_positive = (self.entry_price, self.quantity, self.initial_risk_quote)
        if any(not _finite(value) or value <= 0 for value in required_positive):
            raise JournalError("entry price, quantity and initial risk must be finite and positive")
        if any(not _finite(value) or value < 0 for value in (self.fees_quote, self.slippage_quote)):
            raise JournalError("fees and slippage must be finite and non-negative")

        if self.status == "open":
            if any(value is not None for value in (
                self.exit_time, self.exit_price, self.gross_pnl_quote,
                self.net_pnl_quote, self.exit_reason,
            )):
                raise JournalError("open trade cannot contain exit fields")
            return

        if not isinstance(self.exit_reason, str) or not self.exit_reason.strip():
            raise JournalError("closed trade requires an exit_reason")
        exit_time = _timestamp(self.exit_time, "exit_time")
        if exit_time < entry:
            raise JournalError("exit_time cannot precede entry_time")
        if not _finite(self.exit_price) or self.exit_price <= 0:
            raise JournalError("closed trade requires a finite positive exit_price")
        if not _finite(self.gross_pnl_quote) or not _finite(self.net_pnl_quote):
            raise JournalError("closed trade requires finite gross and net PnL")
        expected = self.gross_pnl_quote - self.fees_quote - self.slippage_quote
        if not isclose(self.net_pnl_quote, expected, rel_tol=1e-9, abs_tol=1e-9):
            raise JournalError("net PnL must equal gross PnL minus fees and slippage")

    @property
    def r_multiple(self) -> float | None:
        if self.status != "closed" or self.net_pnl_quote is None:
            return None
        return self.net_pnl_quote / self.initial_risk_quote


class TradeJournal:
    """JSONL journal; records are append-only and trade IDs are unique."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def records(self) -> tuple[TradeRecord, ...]:
        if not self.path.exists():
            return ()
        found: list[TradeRecord] = []
        seen: set[str] = set()
        try:
            with self.path.open("r", encoding="utf-8") as stream:
                for line_number, line in enumerate(stream, 1):
                    if not line.strip():
                        continue
                    try:
                        raw = json.loads(line)
                        record = TradeRecord(**raw)
                        record.validate()
                    except (json.JSONDecodeError, TypeError, JournalError) as exc:
                        raise JournalError(f"journal corruption at line {line_number}") from exc
                    if record.trade_id in seen:
                        raise JournalError(f"duplicate trade_id at line {line_number}")
                    seen.add(record.trade_id)
                    found.append(record)
        except OSError as exc:
            raise JournalError("journal cannot be read") from exc
        return tuple(found)

    def append(self, record: TradeRecord) -> None:
        if not isinstance(record, TradeRecord):
            raise JournalError("record must be a TradeRecord")
        record.validate()
        if any(existing.trade_id == record.trade_id for existing in self.records()):
            raise JournalError("trade_id already exists; journal is append-only")
        payload = json.dumps(asdict(record), ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="") as stream:
                stream.write(payload + "\n")
                stream.flush()
        except (OSError, TypeError, ValueError) as exc:
            raise JournalError("journal record could not be appended") from exc
