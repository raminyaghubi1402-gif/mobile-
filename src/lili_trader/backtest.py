"""Deterministic, long-only historical backtester. Never connects to an exchange."""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from collections.abc import Sequence


@dataclass(frozen=True)
class BacktestTrade:
    entry_timestamp: int
    exit_timestamp: int
    entry_price: float
    exit_price: float
    quantity: float
    gross_pnl: float
    fees: float
    net_pnl: float
    exit_reason: str


@dataclass(frozen=True)
class BacktestResult:
    starting_cash: float
    ending_equity: float
    net_profit: float
    return_pct: float
    max_drawdown_pct: float
    win_rate_pct: float
    profit_factor: float | None
    trade_count: int
    trades: tuple[BacktestTrade, ...]
    equity_curve: tuple[float, ...]
    warnings: tuple[str, ...]


def run_sma_backtest(
    candles: Sequence[object],
    *,
    starting_cash: float = 1_000_000.0,
    fast_period: int = 7,
    slow_period: int = 25,
    risk_fraction: float = 0.01,
    stop_loss_fraction: float = 0.02,
    take_profit_fraction: float = 0.04,
    fee_rate: float = 0.001,
    slippage_rate: float = 0.001,
) -> BacktestResult:
    """Trade SMA crossovers at the *next* candle open; stop wins ambiguous bars.

    Candle objects must expose timestamp/open/high/low/close. The engine is
    deliberately long-only, uses no future candle to create a signal, and
    applies adverse slippage plus fees on both entry and exit.
    """
    if not candles:
        raise ValueError("at least one candle is required")
    if not isfinite(starting_cash) or starting_cash <= 0:
        raise ValueError("starting_cash must be finite and positive")
    if isinstance(fast_period, bool) or isinstance(slow_period, bool) or fast_period < 1 or slow_period <= fast_period:
        raise ValueError("periods must be positive and slow_period > fast_period")
    for name, value, upper in (
        ("risk_fraction", risk_fraction, 1.0),
        ("stop_loss_fraction", stop_loss_fraction, 1.0),
        ("take_profit_fraction", take_profit_fraction, 10.0),
        ("fee_rate", fee_rate, 1.0),
        ("slippage_rate", slippage_rate, 0.1),
    ):
        if not isfinite(value) or value <= 0 or value >= upper:
            raise ValueError(f"{name} is outside its allowed range")

    bars = []
    last_timestamp = -1
    for candle in candles:
        try:
            ts = int(candle.timestamp)
            o, h, low, close = (float(candle.open), float(candle.high),
                                float(candle.low), float(candle.close))
        except (AttributeError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError("invalid candle structure") from exc
        if ts <= last_timestamp:
            raise ValueError("candles must be strictly chronological")
        if any(not isfinite(v) or v <= 0 for v in (o, h, low, close)):
            raise ValueError("OHLC prices must be finite and positive")
        if low > min(o, close) or h < max(o, close) or low > h:
            raise ValueError("inconsistent OHLC candle")
        bars.append((ts, o, h, low, close))
        last_timestamp = ts

    cash = float(starting_cash)
    position = None
    trades = []
    curve = [cash]
    peak = cash
    max_dd = 0.0

    def close_position(ts, raw_price, reason):
        nonlocal cash, position
        entry, qty, entry_fee, stop, target = position
        fill = raw_price * (1 - slippage_rate)
        exit_fee = fill * qty * fee_rate
        gross = (fill - entry) * qty
        net = gross - entry_fee - exit_fee
        cash += gross - exit_fee
        trades.append(BacktestTrade(
            entry_timestamp=entry[0], exit_timestamp=ts,
            entry_price=entry[1], exit_price=fill, quantity=qty,
            gross_pnl=gross, fees=entry_fee + exit_fee,
            net_pnl=net, exit_reason=reason,
        ))
        position = None

    for i, bar in enumerate(bars):
        ts, o, h, low, close = bar
        # Existing stops/targets are checked before any new signal.
        if position is not None:
            _, _, _, stop, target = position
            if low <= stop:
                close_position(ts, stop, "stop_loss")
            elif h >= target:
                close_position(ts, target, "take_profit")

        # Signal uses only closes up to i-1; execution is at this bar's open.
        signal = False
        signal_idx = i - 1
        if signal_idx >= slow_period:
            closes = [b[4] for b in bars[:signal_idx + 1]]
            fast_now = sum(closes[-fast_period:]) / fast_period
            slow_now = sum(closes[-slow_period:]) / slow_period
            fast_prev = sum(closes[-fast_period-1:-1]) / fast_period
            slow_prev = sum(closes[-slow_period-1:-1]) / slow_period
            signal = fast_prev <= slow_prev and fast_now > slow_now

        if position is None and signal:
            entry_fill = o * (1 + slippage_rate)
            stop = entry_fill * (1 - stop_loss_fraction)
            target = entry_fill * (1 + take_profit_fraction)
            risk_cash = cash * risk_fraction
            qty = risk_cash / (entry_fill - stop)
            qty = min(qty, cash / (entry_fill * (1 + fee_rate)))
            entry_fee = entry_fill * qty * fee_rate
            if qty > 0 and isfinite(qty):
                cash -= entry_fee
                position = ((ts, entry_fill), qty, entry_fee, stop, target)
                # Conservative intrabar check also applies to the entry candle.
                if low <= stop:
                    close_position(ts, stop, "stop_loss")
                elif h >= target:
                    close_position(ts, target, "take_profit")

        mark = cash if position is None else cash + position[1] * close
        curve.append(mark)
        peak = max(peak, mark)
        if peak > 0:
            max_dd = max(max_dd, (peak - mark) / peak * 100)

    if position is not None:
        ts, _, _, close = bars[-1]
        close_position(ts, close, "end_of_data")
        curve.append(cash)
        peak = max(peak, cash)
        if peak > 0:
            max_dd = max(max_dd, (peak - cash) / peak * 100)

    wins = sum(1 for t in trades if t.net_pnl > 0)
    gross_wins = sum(t.net_pnl for t in trades if t.net_pnl > 0)
    gross_losses = -sum(t.net_pnl for t in trades if t.net_pnl < 0)
    pf = gross_wins / gross_losses if gross_losses > 0 else (None if gross_wins == 0 else float("inf"))
    ending = cash
    return BacktestResult(
        starting_cash=starting_cash, ending_equity=ending,
        net_profit=ending - starting_cash,
        return_pct=(ending / starting_cash - 1) * 100,
        max_drawdown_pct=max_dd,
        win_rate_pct=(wins / len(trades) * 100) if trades else 0.0,
        profit_factor=pf, trade_count=len(trades),
        trades=tuple(trades), equity_curve=tuple(curve),
        warnings=(
            "نتیجهٔ گذشته تضمین‌کنندهٔ عملکرد آینده نیست.",
            "کارمزد و لغزش قیمت لحاظ شده؛ funding، مالیات و اثر بازار لحاظ نشده‌اند.",
            "اگر حد ضرر و حد سود در یک کندل لمس شوند، حد ضرر زودتر فرض شده است.",
            "این نسخه فقط لانگ و فقط شبیه‌سازی تاریخی است؛ سفارش واقعی ندارد.",
        ),
    )
