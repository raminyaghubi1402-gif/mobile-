"""Import completed three-timeframe backtest trades into a local journal."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256

from .journal import JournalError, TradeJournal, TradeRecord


class BacktestJournalError(ValueError):
    """A historical backtest result cannot be safely journaled."""


def _iso_utc(timestamp: int) -> str:
    if type(timestamp) is not int or timestamp < 0:
        raise BacktestJournalError("backtest timestamps must be non-negative integers")
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def append_backtest_result(
    result,
    journal: TradeJournal,
    *,
    symbol: str,
    strategy: str = "price_action_1h_15m_5m",
) -> int:
    """Append closed trades from one completed backtest; repeated imports are idempotent.

    The journal stores gross PnL before fees/slippage, plus the separately
    recorded costs, so net PnL remains equal to gross minus both costs.
    """
    if not isinstance(journal, TradeJournal):
        raise BacktestJournalError("journal must be a TradeJournal")
    if not isinstance(symbol, str) or not symbol.strip():
        raise BacktestJournalError("symbol is required")
    if not isinstance(strategy, str) or not strategy.strip():
        raise BacktestJournalError("strategy is required")
    trades = getattr(result, "trades", None)
    if not isinstance(trades, tuple):
        raise BacktestJournalError("result must contain an immutable trades tuple")

    existing = {record.trade_id: record for record in journal.records()}
    appended = 0
    for trade in trades:
        direction = getattr(trade, "direction", None)
        if direction not in ("BUY", "SELL"):
            raise BacktestJournalError("unsupported backtest trade direction")
        quantity = getattr(trade, "quantity", None)
        risk = getattr(trade, "initial_risk_quote", None)
        fees = getattr(trade, "fees", None)
        slippage = getattr(trade, "slippage_quote", None)
        net = getattr(trade, "net_pnl", None)
        values = (quantity, risk, fees, slippage, net)
        if any(
            isinstance(value, bool) or not isinstance(value, (int, float))
            for value in values
        ):
            raise BacktestJournalError("backtest trade contains invalid numeric values")
        if quantity <= 0 or risk <= 0 or fees < 0 or slippage < 0:
            raise BacktestJournalError("backtest quantity, risk or costs are invalid")
        gross = float(net) + float(fees) + float(slippage)
        trade_id_source = "|".join((
            symbol.upper(), strategy, direction,
            str(getattr(trade, "entry_timestamp", "")),
            str(getattr(trade, "exit_timestamp", "")),
        ))
        trade_id = "bt-" + sha256(trade_id_source.encode("utf-8")).hexdigest()[:24]
        record = TradeRecord(
            trade_id=trade_id,
            symbol=symbol.upper(),
            strategy=strategy,
            side="long" if direction == "BUY" else "short",
            status="closed",
            entry_time=_iso_utc(trade.entry_timestamp),
            entry_price=trade.entry_price,
            quantity=quantity,
            initial_risk_quote=risk,
            entry_reason="ورود تأییدشده توسط موتور بک‌تست سه‌تایم‌فریمی",
            exit_time=_iso_utc(trade.exit_timestamp),
            exit_price=trade.exit_price,
            gross_pnl_quote=gross,
            fees_quote=fees,
            slippage_quote=slippage,
            net_pnl_quote=net,
            exit_reason=trade.exit_reason,
        )
        record.validate()
        previous = existing.get(trade_id)
        if previous is not None:
            if previous != record:
                raise BacktestJournalError("same backtest trade ID has different contents")
            continue
        try:
            journal.append(record)
        except JournalError as exc:
            raise BacktestJournalError("backtest trade could not be journaled") from exc
        existing[trade_id] = record
        appended += 1
    return appended
