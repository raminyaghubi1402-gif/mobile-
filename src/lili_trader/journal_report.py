"Deterministic performance statistics for the local append-only paper journal."
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Optional

from .journal import TradeJournal, TradeRecord


@dataclass(frozen=True)
class JournalPerformance:
    starting_equity: float
    ending_equity: float
    total_records: int
    open_trades: int
    closed_trades: int
    wins: int
    losses: int
    breakeven: int
    win_rate_pct: float
    net_profit_quote: float
    gross_pnl_quote: float
    fees_quote: float
    slippage_quote: float
    profit_factor: Optional[float]
    expectancy_quote: float
    average_r: Optional[float]
    max_drawdown_quote: float
    max_drawdown_pct: float
    best_trade_net: Optional[float]
    worst_trade_net: Optional[float]
    max_losing_streak: int


def _timestamp(record: TradeRecord) -> datetime:
    return datetime.fromisoformat(record.exit_time.replace("Z", "+00:00"))


def summarize_journal(
    journal: TradeJournal,
    *,
    starting_equity: float,
) -> JournalPerformance:
    """Summarize closed journal trades in exit-time order, including all costs."""
    if not isinstance(journal, TradeJournal):
        raise TypeError("journal must be a TradeJournal")
    if (
        isinstance(starting_equity, bool)
        or not isinstance(starting_equity, (int, float))
        or not isfinite(starting_equity)
        or starting_equity <= 0
    ):
        raise ValueError("starting_equity must be finite and positive")

    records = journal.records()
    closed = sorted(
        (record for record in records if record.status == "closed"),
        key=_timestamp,
    )
    net_values = [float(record.net_pnl_quote) for record in closed]
    wins = sum(value > 0 for value in net_values)
    losses = sum(value < 0 for value in net_values)
    breakeven = len(net_values) - wins - losses
    net_wins = sum(value for value in net_values if value > 0)
    net_losses = -sum(value for value in net_values if value < 0)
    profit_factor = net_wins / net_losses if net_losses > 0 else None
    net_profit = sum(net_values)
    equity = float(starting_equity)
    peak = equity
    max_drawdown = 0.0
    losing_streak = 0
    max_losing_streak = 0
    for value in net_values:
        equity += value
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
        if value < 0:
            losing_streak += 1
            max_losing_streak = max(max_losing_streak, losing_streak)
        else:
            losing_streak = 0

    count = len(closed)
    return JournalPerformance(
        starting_equity=float(starting_equity),
        ending_equity=float(starting_equity) + net_profit,
        total_records=len(records),
        open_trades=sum(record.status == "open" for record in records),
        closed_trades=count,
        wins=wins,
        losses=losses,
        breakeven=breakeven,
        win_rate_pct=(wins / count * 100) if count else 0.0,
        net_profit_quote=net_profit,
        gross_pnl_quote=sum(float(record.gross_pnl_quote) for record in closed),
        fees_quote=sum(record.fees_quote for record in closed),
        slippage_quote=sum(record.slippage_quote for record in closed),
        profit_factor=profit_factor,
        expectancy_quote=(net_profit / count) if count else 0.0,
        average_r=(
            sum(float(record.r_multiple) for record in closed) / count
            if count else None
        ),
        max_drawdown_quote=max_drawdown,
        max_drawdown_pct=max_drawdown / peak * 100 if peak > 0 else 0.0,
        best_trade_net=max(net_values) if net_values else None,
        worst_trade_net=min(net_values) if net_values else None,
        max_losing_streak=max_losing_streak,
    )


def render_journal_report(summary: JournalPerformance) -> str:
    """Render a concise Persian report; never invent a trade recommendation."""
    if not isinstance(summary, JournalPerformance):
        raise TypeError("summary must be a JournalPerformance")
    factor = (
        f"{summary.profit_factor:.2f}"
        if summary.profit_factor is not None
        else "قابل محاسبه نیست (زیان خالص ثبت نشده)"
    )
    average_r = f"{summary.average_r:.2f}R" if summary.average_r is not None else "—"
    best = f"{summary.best_trade_net:.2f}" if summary.best_trade_net is not None else "—"
    worst = f"{summary.worst_trade_net:.2f}" if summary.worst_trade_net is not None else "—"
    return "\n".join((
        "گزارش عملکرد ژورنال معاملات آزمایشی",
        f"تعداد معاملات بسته: {summary.closed_trades}",
        f"معاملات باز: {summary.open_trades}",
        f"برد / باخت / سربه‌سر: {summary.wins} / {summary.losses} / {summary.breakeven}",
        f"نرخ برد: {summary.win_rate_pct:.2f}٪",
        f"سود خالص: {summary.net_profit_quote:.2f}",
        f"سود ناخالص ثبت‌شده: {summary.gross_pnl_quote:.2f}",
        f"کارمزدها: {summary.fees_quote:.2f}",
        f"لغزش ثبت‌شده: {summary.slippage_quote:.2f}",
        f"ضریب سود خالص: {factor}",
        f"امید ریاضی هر معامله: {summary.expectancy_quote:.2f}",
        f"میانگین R: {average_r}",
        f"بیشترین افت سرمایه: {summary.max_drawdown_quote:.2f} ({summary.max_drawdown_pct:.2f}٪)",
        f"بهترین / بدترین معامله: {best} / {worst}",
        f"بیشترین باخت متوالی: {summary.max_losing_streak}",
        f"سرمایه شروع / پایان محاسباتی: {summary.starting_equity:.2f} / {summary.ending_equity:.2f}",
        "این گزارش فقط معاملات ثبت‌شده در ژورنال را خلاصه می‌کند؛ تضمین سود یا سیگنال خریدوفروش نیست.",
    ))
