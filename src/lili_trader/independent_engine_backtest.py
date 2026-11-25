"""Isolated historical paper tests for each registered strategy engine.

Each strategy receives its own virtual cash ledger so one engine's signal or
position cannot confirm, block, or consume another engine's opportunity.
Inputs must be a single contiguous segment per timeframe.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from math import isfinite
from typing import Any, Sequence

from .market_regime import classify_market_regime, select_strategy_candidates
from .nobitex_data import NobitexCandle
from .price_action_trend import classify_price_action_trend
from .strategy_engines import build_default_strategy_registry
from .engine_snapshot_builder import build_independent_engine_snapshots


@dataclass(frozen=True)
class EngineTrade:
    strategy_id: str
    entry_timestamp: int
    exit_timestamp: int
    entry_price: float
    exit_price: float
    quantity: float
    net_pnl: float
    fees: float
    exit_reason: str


@dataclass(frozen=True)
class EngineStats:
    strategy_id: str
    starting_cash: float
    ending_equity: float
    net_profit: float
    return_pct: float
    max_drawdown_pct: float
    win_rate_pct: float
    profit_factor: float | None
    trade_count: int
    evaluated_bars: int
    signal_count: int
    trades: tuple[EngineTrade, ...]


@dataclass(frozen=True)
class IndependentEngineBacktestResult:
    symbol: str
    bars: int
    first_timestamp: int
    last_timestamp: int
    engines: tuple[EngineStats, ...]
    warnings: tuple[str, ...]


def _validate_series(candles: Sequence[NobitexCandle], resolution: str,
                     seconds: int, name: str) -> list[NobitexCandle]:
    if not isinstance(candles, Sequence) or len(candles) < 30:
        raise ValueError(f"{name} needs at least 30 contiguous candles")
    rows = list(candles)
    previous = None
    symbol = None
    for candle in rows:
        if not isinstance(candle, NobitexCandle) or candle.resolution != resolution:
            raise ValueError(f"{name} contains a candle with the wrong type/resolution")
        if symbol is None:
            symbol = candle.symbol
        if candle.symbol != symbol:
            raise ValueError(f"{name} mixes symbols")
        values = (candle.open, candle.high, candle.low, candle.close, candle.volume)
        if any(isinstance(v, bool) or not isinstance(v, (int, float))
               or not isfinite(float(v)) for v in values):
            raise ValueError(f"{name} contains non-finite OHLCV")
        o, h, low, close, volume = (float(v) for v in values)
        if min(o, h, low, close) <= 0 or volume < 0:
            raise ValueError(f"{name} contains non-positive prices or negative volume")
        if low > min(o, close) or h < max(o, close) or low > h:
            raise ValueError(f"{name} contains inconsistent OHLC")
        if type(candle.timestamp) is not int or candle.timestamp <= 0:
            raise ValueError(f"{name} has an invalid timestamp")
        if previous is not None and candle.timestamp != previous + seconds:
            raise ValueError(f"{name} is not one contiguous segment")
        previous = candle.timestamp
    return rows


def _closed_copy(candle: NobitexCandle) -> NobitexCandle:
    return NobitexCandle(
        symbol=candle.symbol, resolution=candle.resolution,
        timestamp=candle.timestamp, open=candle.open, high=candle.high,
        low=candle.low, close=candle.close, volume=candle.volume,
        source=candle.source, fetched_at=candle.timestamp + (
            300 if candle.resolution == "5" else
            900 if candle.resolution == "15" else 3600
        ),
    )


def run_independent_engine_backtest(
    hourly_candles: Sequence[NobitexCandle],
    fifteen_minute_candles: Sequence[NobitexCandle],
    five_minute_candles: Sequence[NobitexCandle],
    *,
    starting_cash: float = 13_550_000.0,
    risk_fraction: float = 0.005,
    fee_rate: float = 0.0025,
    slippage_rate: float = 0.001,
    minimum_order_quote: float | None = 3_000_000.0,
    maximum_5m_bars: int | None = None,
) -> IndependentEngineBacktestResult:
    """Evaluate each engine on closed history with a separate spot-long ledger.

    A signal at the close of bar i can enter only at bar i+1 open. Every
    timeframe is cut off at its own close time before each decision. The caller
    must pass one contiguous segment for each timeframe; gaps are rejected.
    """
    for name, value in (("starting_cash", starting_cash),
                        ("risk_fraction", risk_fraction),
                        ("fee_rate", fee_rate),
                        ("slippage_rate", slippage_rate)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
            raise ValueError(f"{name} must be finite numeric")
    if starting_cash <= 0 or not 0 < risk_fraction <= 0.02:
        raise ValueError("invalid cash or risk fraction")
    if not 0 <= fee_rate < 0.05 or not 0 <= slippage_rate < 0.05:
        raise ValueError("invalid fee or slippage rate")
    if minimum_order_quote is not None and (
        isinstance(minimum_order_quote, bool)
        or not isinstance(minimum_order_quote, (int, float))
        or not isfinite(minimum_order_quote) or minimum_order_quote <= 0
    ):
        raise ValueError("minimum_order_quote must be finite and positive")
    if maximum_5m_bars is not None and (
        type(maximum_5m_bars) is not int or maximum_5m_bars < 24
    ):
        raise ValueError("maximum_5m_bars must be >= 24")

    hourly = _validate_series(hourly_candles, "60", 3600, "1h")
    fifteen = _validate_series(fifteen_minute_candles, "15", 900, "15m")
    five = _validate_series(five_minute_candles, "5", 300, "5m")
    symbols = {hourly[0].symbol, fifteen[0].symbol, five[0].symbol}
    if len(symbols) != 1:
        raise ValueError("timeframes must belong to the same symbol")
    symbol = next(iter(symbols))
    overlap_start = max(hourly[0].timestamp, fifteen[0].timestamp, five[0].timestamp)
    overlap_end = min(hourly[-1].timestamp + 3600,
                      fifteen[-1].timestamp + 900, five[-1].timestamp + 300)
    if overlap_end <= overlap_start:
        raise ValueError("the three timeframes have no overlapping historical interval")
    if maximum_5m_bars is not None:
        five = five[-maximum_5m_bars:]

    hourly_close_times = [c.timestamp + 3600 for c in hourly]
    fifteen_close_times = [c.timestamp + 900 for c in fifteen]
    registry = build_default_strategy_registry()
    engine_ids = registry.registered_ids
    ledgers: dict[str, dict[str, Any]] = {
        engine_id: {
            "cash": float(starting_cash), "position": None, "pending": None,
            "trades": [], "curve": [float(starting_cash)],
            "evaluated": 0, "signals": 0, "peak": float(starting_cash),
            "max_dd": 0.0,
        } for engine_id in engine_ids
    }

    def close_position(engine_id: str, ledger: dict[str, Any],
                       candle: NobitexCandle, raw_exit: float,
                       reason: str) -> None:
        position = ledger["position"]
        exit_fill = raw_exit * (1 - slippage_rate)
        exit_fee = exit_fill * position["quantity"] * fee_rate
        net = ((exit_fill - position["entry_fill"]) * position["quantity"]
               - position["entry_fee"] - exit_fee)
        ledger["cash"] += exit_fill * position["quantity"] - exit_fee
        ledger["trades"].append(EngineTrade(
            strategy_id=engine_id,
            entry_timestamp=position["entry_timestamp"],
            exit_timestamp=candle.timestamp + 300,
            entry_price=position["entry_fill"],
            exit_price=exit_fill,
            quantity=position["quantity"],
            net_pnl=net,
            fees=position["entry_fee"] + exit_fee,
            exit_reason=reason,
        ))
        ledger["position"] = None

    first_index = max(23, 0)
    for index in range(first_index, len(five) - 1):
        signal_candle = five[index]
        signal_close_time = signal_candle.timestamp + 300
        h_count = bisect_right(hourly_close_times, signal_close_time)
        m_count = bisect_right(fifteen_close_times, signal_close_time)
        h_closed = hourly[:h_count]
        m_closed = fifteen[:m_count]
        if len(h_closed) < 30 or len(m_closed) < 30:
            continue
        if signal_close_time - hourly_close_times[h_count - 1] > 7200:
            continue
        if signal_close_time - fifteen_close_times[m_count - 1] > 1800:
            continue

        five_window = [_closed_copy(row) for row in five[max(0, index - 299):index + 1]]
        hourly_window = [_closed_copy(row) for row in h_closed[-100:]]
        fifteen_window = [_closed_copy(row) for row in m_closed[-200:]]
        try:
            snapshots = build_independent_engine_snapshots(
                hourly_window, fifteen_window, five_window, now=signal_close_time
            )
            trend_15m = classify_price_action_trend(
                fifteen_window, pivot_window=2, min_swings=2
            )
            regime = classify_market_regime(
                fifteen_window, trend_15m, volatility_window=20
            )
            candidates = select_strategy_candidates(regime, allow_short=False)
            selected = {
                item.name for item in candidates if item.eligible
            }
            if regime.trend == "up" and regime.volatility in {"low", "normal"}:
                decisions = {
                    engine_id: registry.evaluate_selected(
                        snapshots[engine_id], (engine_id,)
                    )[0]
                    for engine_id in engine_ids if engine_id in selected
                }
            else:
                decisions = {}
        except (ValueError, TypeError, IndexError):
            decisions = {}

        execution_candle = five[index + 1]
        for engine_id, ledger in ledgers.items():
            occupied_at_start = ledger["position"] is not None or ledger["pending"] is not None
            position = ledger["position"]
            pending = ledger["pending"]

            if pending is not None:
                if execution_candle.timestamp != pending["signal_timestamp"] + 300:
                    ledger["pending"] = None
                else:
                    raw_entry = float(execution_candle.open)
                    entry_fill = raw_entry * (1 + slippage_rate)
                    stop = pending["stop"]
                    target = pending["target"]
                    stop_fill = stop * (1 - slippage_rate)
                    unit_risk = entry_fill - stop_fill + (entry_fill + stop_fill) * fee_rate
                    actual_risk = entry_fill - stop
                    actual_reward = target - entry_fill
                    actual_rr = actual_reward / actual_risk if actual_risk > 0 else 0.0
                    if raw_entry > stop and raw_entry < target and unit_risk > 0 and actual_rr >= 1.5:
                        risk_budget = ledger["cash"] * risk_fraction
                        quantity = min(
                            risk_budget / unit_risk,
                            ledger["cash"] / (entry_fill * (1 + fee_rate)),
                        )
                        if (quantity > 0 and isfinite(quantity)
                                and (minimum_order_quote is None
                                     or entry_fill * quantity >= minimum_order_quote)):
                            entry_fee = entry_fill * quantity * fee_rate
                            ledger["cash"] -= entry_fill * quantity + entry_fee
                            ledger["position"] = {
                                "entry_timestamp": execution_candle.timestamp,
                                "entry_fill": entry_fill,
                                "quantity": quantity,
                                "entry_fee": entry_fee,
                                "stop": stop,
                                "target": target,
                            }
                    ledger["pending"] = None

            position = ledger["position"]
            if position is not None:
                stop_hit = execution_candle.low <= position["stop"]
                target_hit = execution_candle.high >= position["target"]
                if stop_hit:
                    raw_exit = min(execution_candle.open, position["stop"])
                    close_position(engine_id, ledger, execution_candle, raw_exit, "stop_loss")
                elif target_hit:
                    close_position(engine_id, ledger, execution_candle,
                                   position["target"], "take_profit")

            if (not occupied_at_start and ledger["position"] is None
                    and ledger["pending"] is None):
                decision = decisions.get(engine_id)
                ledger["evaluated"] += 1
                if decision is not None and decision.status == "SIGNAL":
                    ledger["signals"] += 1
                    ledger["pending"] = {
                        "signal_timestamp": signal_candle.timestamp,
                        "stop": float(decision.evidence["stop_loss"]),
                        "target": float(decision.evidence["take_profit"]),
                    }

            position = ledger["position"]
            if position is None:
                mark = ledger["cash"]
            else:
                estimated_exit = execution_candle.close * (1 - slippage_rate)
                estimated_fee = estimated_exit * position["quantity"] * fee_rate
                mark = ledger["cash"] + position["quantity"] * estimated_exit - estimated_fee
            ledger["curve"].append(mark)
            ledger["peak"] = max(ledger["peak"], mark)
            if ledger["peak"] > 0:
                ledger["max_dd"] = max(
                    ledger["max_dd"], (ledger["peak"] - mark) / ledger["peak"] * 100
                )

    if not any(ledger["evaluated"] for ledger in ledgers.values()):
        raise ValueError("no overlapping closed-candle window with enough 1h/15m/5m history")

    last_candle = five[-1]
    results = []
    for engine_id, ledger in ledgers.items():
        if ledger["position"] is not None:
            close_position(engine_id, ledger, last_candle,
                           float(last_candle.close), "end_of_data")
            ledger["curve"].append(ledger["cash"])
            ledger["peak"] = max(ledger["peak"], ledger["cash"])
            if ledger["peak"] > 0:
                ledger["max_dd"] = max(
                    ledger["max_dd"],
                    (ledger["peak"] - ledger["cash"]) / ledger["peak"] * 100,
                )
        trades = tuple(ledger["trades"])
        net = ledger["cash"] - starting_cash
        wins = [trade.net_pnl for trade in trades if trade.net_pnl > 0]
        losses = [-trade.net_pnl for trade in trades if trade.net_pnl < 0]
        gross_wins, gross_losses = sum(wins), sum(losses)
        profit_factor = (
            gross_wins / gross_losses if gross_losses > 0
            else (float("inf") if gross_wins > 0 else None)
        )
        results.append(EngineStats(
            strategy_id=engine_id,
            starting_cash=starting_cash,
            ending_equity=ledger["cash"],
            net_profit=net,
            return_pct=net / starting_cash * 100,
            max_drawdown_pct=ledger["max_dd"],
            win_rate_pct=(len(wins) / len(trades) * 100) if trades else 0.0,
            profit_factor=profit_factor,
            trade_count=len(trades),
            evaluated_bars=ledger["evaluated"],
            signal_count=ledger["signals"],
            trades=trades,
        ))

    return IndependentEngineBacktestResult(
        symbol=symbol,
        bars=len(five),
        first_timestamp=five[0].timestamp,
        last_timestamp=five[-1].timestamp,
        engines=tuple(results),
        warnings=(
            "هر موتور حساب آزمایشی مستقل دارد؛ نتایج را با هم جمع نکنید.",
            "ورود فقط در بازشدن کندل ۵دقیقهٔ بعد از سیگنال انجام می‌شود.",
            f"کارمزد پایهٔ {fee_rate * 100:.2f}% و لغزش {slippage_rate * 100:.2f}% اعمال شده‌اند.",
            f"حداقل ارزش سفارش: {minimum_order_quote:,.0f} IRT." if minimum_order_quote is not None else "حداقل ارزش سفارش اعمال نشده است.",
            "عمق دفتر سفارش، اثر بازار و مالیات لحاظ نشده‌اند.",
            "اگر حدضرر و هدف در یک کندل لمس شوند، حدضرر زودتر فرض می‌شود.",
            "این بک‌تست فقط یک قطعهٔ پیوسته را می‌پذیرد و شکاف‌ها را رد می‌کند.",
            "نتایج تاریخی تضمین‌کنندهٔ سودآوری آینده نیستند.",
        ),
    )
