"Offline, long-only paper-trade lifecycle backed by the local append-only journal."
from __future__ import annotations

from datetime import datetime, timezone
from math import isclose, isfinite
from typing import Optional

from .journal import TradeJournal, TradeRecord
from .paper import PaperAccount, PaperFill


class PaperSessionError(ValueError):
    """A paper trade cannot safely advance to the requested lifecycle state."""


def _valid_cost(value: float, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PaperSessionError(f"{field} must be a finite non-negative number")
    if not isfinite(value) or value < 0:
        raise PaperSessionError(f"{field} must be a finite non-negative number")
    return float(value)


def _now_or(value: Optional[str]) -> str:
    return value if value is not None else datetime.now(timezone.utc).isoformat()


class PaperTradeSession:
    """Connect a spot-only PaperAccount to TradeJournal.

    This adapter supports one open long trade at a time. It never accesses a
    broker or network. Every fill is stored with an account snapshot in the
    append-only journal. The restore() classmethod replays those events and
    verifies cash, position, fees and order IDs before returning a usable session.
    """

    def __init__(self, account: PaperAccount, journal: TradeJournal) -> None:
        if not isinstance(account, PaperAccount):
            raise PaperSessionError("account must be a PaperAccount")
        if not isinstance(journal, TradeJournal):
            raise PaperSessionError("journal must be a TradeJournal")
        self.account = account
        self.journal = journal

    @classmethod
    def restore(cls, journal: TradeJournal) -> "PaperTradeSession":
        """Rebuild the paper account by replaying durable account snapshots.

        Every event must contain a complete account snapshot. Mixed or legacy
        journals fail closed rather than guessing the account balance.
        """
        events = journal.events()
        if not events:
            raise PaperSessionError("journal has no account events to restore")
        first = events[0]
        if first.account_starting_cash is None:
            raise PaperSessionError("journal does not contain restorable account snapshots")
        starting_cash = first.account_starting_cash
        fee_rate = first.account_fee_rate
        account = PaperAccount(starting_cash, fee_rate)
        for index, event in enumerate(events, 1):
            if event.account_order_id is None:
                raise PaperSessionError(f"account snapshot missing at journal event {index}")
            if (
                event.account_starting_cash != starting_cash
                or event.account_fee_rate != fee_rate
            ):
                raise PaperSessionError("paper account configuration changed within journal")
            try:
                fill = account.execute(
                    event.account_order_id,
                    event.account_fill_side,
                    event.account_fill_quantity,
                    event.account_fill_price,
                )
            except (ValueError, TypeError) as exc:
                raise PaperSessionError(f"cannot replay account event {index}") from exc
            checks = (
                (fill.fee_quote, event.account_fill_fee_quote),
                (fill.cash_after, event.account_cash_after),
                (fill.position_after, event.account_position_after),
            )
            if any(not isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9)
                   for actual, expected in checks):
                raise PaperSessionError(f"account snapshot mismatch at journal event {index}")
        open_records = [record for record in journal.records() if record.status == "open"]
        expected_position = sum(record.quantity for record in open_records if record.side == "long")
        if len(open_records) > 1 or not isclose(
            account.position, expected_position, rel_tol=1e-9, abs_tol=1e-9
        ):
            raise PaperSessionError("restored account position does not match open journal trades")
        return cls(account, journal)

    def _open_records(self) -> list[TradeRecord]:
        return [record for record in self.journal.records() if record.status == "open"]

    def open_trade(
        self,
        *,
        trade_id: str,
        symbol: str,
        strategy: str,
        quantity: float,
        entry_price: float,
        initial_risk_quote: float,
        entry_reason: str,
        entry_time: Optional[str] = None,
        entry_slippage_quote: float = 0.0,
    ) -> TradeRecord:
        """Buy one simulated spot position and append its open journal event."""
        if not isinstance(trade_id, str) or not trade_id.strip():
            raise PaperSessionError("trade_id is required")
        if not isinstance(symbol, str) or not symbol.strip():
            raise PaperSessionError("symbol is required")
        if not isinstance(strategy, str) or not strategy.strip():
            raise PaperSessionError("strategy is required")
        if not isinstance(entry_reason, str) or not entry_reason.strip():
            raise PaperSessionError("entry_reason is required")
        if isinstance(initial_risk_quote, bool) or not isinstance(initial_risk_quote, (int, float)):
            raise PaperSessionError("initial_risk_quote must be finite and positive")
        if not isfinite(initial_risk_quote) or initial_risk_quote <= 0:
            raise PaperSessionError("initial_risk_quote must be finite and positive")
        entry_slippage_quote = _valid_cost(entry_slippage_quote, "entry_slippage_quote")

        existing = self.journal.records()
        if any(record.trade_id == trade_id for record in existing):
            raise PaperSessionError("trade_id has already been used")
        if self._open_records():
            raise PaperSessionError("another paper trade is already open")
        if self.account.position != 0:
            raise PaperSessionError("account must have zero position before opening a journaled trade")

        created: list[TradeRecord] = []
        order_id = f"{trade_id}:entry"

        def persist_open(fill: PaperFill) -> None:
            record = TradeRecord(
                trade_id=trade_id,
                symbol=symbol,
                strategy=strategy,
                side="long",
                status="open",
                entry_time=_now_or(entry_time),
                entry_price=fill.price,
                quantity=fill.quantity,
                initial_risk_quote=float(initial_risk_quote),
                entry_reason=entry_reason,
                fees_quote=fill.fee_quote,
                slippage_quote=entry_slippage_quote,
                account_order_id=fill.order_id,
                account_fill_side=fill.side,
                account_fill_quantity=fill.quantity,
                account_fill_price=fill.price,
                account_fill_fee_quote=fill.fee_quote,
                account_cash_after=fill.cash_after,
                account_position_after=fill.position_after,
                account_starting_cash=self.account.starting_cash,
                account_fee_rate=self.account.fee_rate,
            )
            self.journal.append(record)
            created.append(record)

        if isinstance(quantity, bool) or not isinstance(quantity, (int, float)) or not isfinite(quantity) or quantity <= 0:
            raise PaperSessionError("quantity must be finite and positive")
        if isinstance(entry_price, bool) or not isinstance(entry_price, (int, float)) or not isfinite(entry_price) or entry_price <= 0:
            raise PaperSessionError("entry_price must be finite and positive")
        effective_entry_price = entry_price + entry_slippage_quote / quantity
        if not isfinite(effective_entry_price) or effective_entry_price <= 0:
            raise PaperSessionError("effective entry price is invalid")
        self.account.execute(
            order_id, "buy", quantity, effective_entry_price, before_commit=persist_open
        )
        if not created:
            raise PaperSessionError("entry order ID already exists in the paper account")
        return created[0]

    def close_trade(
        self,
        *,
        exit_price: float,
        exit_reason: str,
        exit_time: Optional[str] = None,
        exit_slippage_quote: float = 0.0,
    ) -> TradeRecord:
        """Sell the complete open spot position and append its closing event."""
        if not isinstance(exit_reason, str) or not exit_reason.strip():
            raise PaperSessionError("exit_reason is required")
        exit_slippage_quote = _valid_cost(exit_slippage_quote, "exit_slippage_quote")
        open_records = self._open_records()
        if len(open_records) != 1:
            raise PaperSessionError("exactly one open journal trade is required")
        opened = open_records[0]
        if not isclose(self.account.position, opened.quantity, rel_tol=1e-9, abs_tol=1e-12):
            raise PaperSessionError(
                "in-memory account position does not match journal; restore it before closing"
            )

        created: list[TradeRecord] = []
        order_id = f"{opened.trade_id}:exit"
        if any(fill.order_id == order_id for fill in self.account.fills):
            raise PaperSessionError("exit order ID already exists in the paper account")

        def persist_close(fill: PaperFill) -> None:
            actual_gross_pnl = (fill.price - opened.entry_price) * opened.quantity
            total_fees = opened.fees_quote + fill.fee_quote
            total_slippage = opened.slippage_quote + exit_slippage_quote
            gross_pnl = actual_gross_pnl + total_slippage
            closed = TradeRecord(
                trade_id=opened.trade_id,
                symbol=opened.symbol,
                strategy=opened.strategy,
                side=opened.side,
                status="closed",
                entry_time=opened.entry_time,
                entry_price=opened.entry_price,
                quantity=opened.quantity,
                initial_risk_quote=opened.initial_risk_quote,
                entry_reason=opened.entry_reason,
                exit_time=_now_or(exit_time),
                exit_price=fill.price,
                gross_pnl_quote=gross_pnl,
                fees_quote=total_fees,
                slippage_quote=total_slippage,
                net_pnl_quote=actual_gross_pnl - total_fees,
                exit_reason=exit_reason,
                account_order_id=fill.order_id,
                account_fill_side=fill.side,
                account_fill_quantity=fill.quantity,
                account_fill_price=fill.price,
                account_fill_fee_quote=fill.fee_quote,
                account_cash_after=fill.cash_after,
                account_position_after=fill.position_after,
                account_starting_cash=self.account.starting_cash,
                account_fee_rate=self.account.fee_rate,
            )
            self.journal.append(closed)
            created.append(closed)

        if isinstance(exit_price, bool) or not isinstance(exit_price, (int, float)) or not isfinite(exit_price) or exit_price <= 0:
            raise PaperSessionError("exit_price must be finite and positive")
        effective_exit_price = exit_price - exit_slippage_quote / opened.quantity
        if not isfinite(effective_exit_price) or effective_exit_price <= 0:
            raise PaperSessionError("exit slippage makes effective exit price non-positive")
        self.account.execute(
            order_id, "sell", opened.quantity, effective_exit_price, before_commit=persist_close
        )
        if not created:
            raise PaperSessionError("exit fill was not journaled")
        return created[0]
