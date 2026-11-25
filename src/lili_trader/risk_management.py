"""Conservative structural trailing-stop rules for spot-long paper positions.

Only confirmed swing lows from closed, contiguous 5m candles may raise a stop.
The stop is never lowered and is not activated if the confirming candle has
already traded through the proposed level. No exchange/order functionality.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from collections.abc import Mapping, Sequence
from typing import Any

from .market_structure import analyze_market_structure


@dataclass(frozen=True)
class StopUpdate:
    stop_loss: float
    swing_low: float
    swing_timestamp: int
    breakeven_floor: float
    atr: float


def fee_adjusted_breakeven_stop(
    entry_fill_price: float, *, fee_rate: float, slippage_rate: float
) -> float:
    """Raw stop price whose modeled exit nets zero after both-side fees/slippage."""
    values = (entry_fill_price, fee_rate, slippage_rate)
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not isfinite(v)
           for v in values):
        raise ValueError("entry and cost rates must be finite numbers")
    if entry_fill_price <= 0 or not 0 <= fee_rate < 1 or not 0 <= slippage_rate < 1:
        raise ValueError("invalid entry price or cost rates")
    denominator = (1.0 - slippage_rate) * (1.0 - fee_rate)
    result = entry_fill_price * (1.0 + fee_rate) / denominator
    if not isfinite(result) or result <= 0:
        raise ValueError("breakeven calculation is not finite and positive")
    return result


def _value(candle: Any, name: str) -> float:
    raw = candle.get(name) if isinstance(candle, Mapping) else getattr(candle, name, None)
    if isinstance(raw, bool):
        raise ValueError(f"{name} must be numeric")
    try:
        result = float(raw)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{name} must be numeric") from None
    if not isfinite(result) or result <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return result


def _timestamp(candle: Any) -> int:
    raw = candle.get("timestamp") if isinstance(candle, Mapping) else getattr(candle, "timestamp", None)
    if type(raw) is not int or raw <= 0:
        raise ValueError("candle timestamp must be a positive integer")
    return raw


def _atr(candles: Sequence[Any], period: int) -> float:
    true_ranges = []
    previous_close = None
    for candle in candles:
        high = _value(candle, "high")
        low = _value(candle, "low")
        close = _value(candle, "close")
        tr = high - low if previous_close is None else max(
            high - low, abs(high - previous_close), abs(low - previous_close)
        )
        true_ranges.append(tr)
        previous_close = close
    sample = true_ranges[-period:]
    if len(sample) < period:
        raise ValueError("not enough closed candles for ATR buffer")
    result = sum(sample) / period
    if not isfinite(result) or result <= 0:
        raise ValueError("ATR must be finite and positive")
    return result


def propose_structural_trailing_stop(
    candles: Sequence[Any],
    *,
    entry_timestamp: int,
    entry_fill_price: float,
    current_stop: float,
    fee_rate: float,
    slippage_rate: float,
    pivot_window: int = 2,
    atr_period: int = 14,
    atr_buffer_fraction: float = 0.25,
) -> StopUpdate | None:
    """Raise (never loosen) a long stop behind a confirmed, unbroken higher low.

    Input candles must be closed, chronological and exactly contiguous at 5m.
    The entry candle must be present. A candidate is accepted only after the
    right-side pivot confirmation candles have closed, while price remains
    above the candidate and the candidate is above fee-adjusted breakeven.
    """
    if not isinstance(candles, Sequence) or isinstance(candles, (str, bytes)):
        raise ValueError("candles must be a sequence")
    if (type(entry_timestamp) is not int or entry_timestamp <= 0
            or type(pivot_window) is not int or pivot_window < 1
            or type(atr_period) is not int or atr_period < 2
            or isinstance(atr_buffer_fraction, bool)
            or not isinstance(atr_buffer_fraction, (int, float))
            or not isfinite(atr_buffer_fraction)
            or not 0 < atr_buffer_fraction <= 1):
        raise ValueError("invalid trailing-stop configuration")
    if (isinstance(entry_fill_price, bool) or not isinstance(entry_fill_price, (int, float))
            or not isfinite(entry_fill_price) or entry_fill_price <= 0
            or isinstance(current_stop, bool) or not isinstance(current_stop, (int, float))
            or not isfinite(current_stop) or current_stop <= 0):
        raise ValueError("entry fill and current stop must be finite positive prices")
    if len(candles) < max(atr_period, 2 * pivot_window + 3):
        return None

    timestamps = [_timestamp(c) for c in candles]
    if any(b - a != 300 for a, b in zip(timestamps, timestamps[1:])):
        return None
    # Every bar must carry source fetch time; never assume an unverified
    # candle was closed when the monitor observed it.
    for candle, timestamp in zip(candles, timestamps):
        fetched = (candle.get("fetched_at") if isinstance(candle, Mapping)
                   else getattr(candle, "fetched_at", None))
        if type(fetched) is not int or fetched < timestamp + 300:
            return None
    try:
        entry_index = timestamps.index(entry_timestamp)
    except ValueError:
        return None
    if entry_index >= len(candles) - 1:
        return None

    analysis = analyze_market_structure(candles, pivot_window=pivot_window)
    if analysis.bias != "up":
        return None
    atr = _atr(candles, atr_period)
    breakeven = fee_adjusted_breakeven_stop(
        float(entry_fill_price), fee_rate=fee_rate, slippage_rate=slippage_rate
    )
    latest = candles[-1]
    latest_close = _value(latest, "close")
    latest_low = _value(latest, "low")

    eligible = [
        swing for swing in analysis.swings
        if swing.kind == "low"
        and swing.index > entry_index
        and swing.index + pivot_window < len(candles)
    ]
    if not eligible:
        return None

    for swing in reversed(eligible):
        candidate = swing.price - atr * float(atr_buffer_fraction)
        # Reject invalidated pivots and any level already crossed since its
        # formation. This prevents retroactively pretending the stop was live.
        post_pivot_lows = [
            _value(c, "low") for c in candles[swing.index + 1:]
        ]
        if post_pivot_lows and min(post_pivot_lows) <= candidate:
            continue
        if not (candidate > float(current_stop)
                and candidate > breakeven
                and candidate < latest_close
                and candidate < latest_low):
            continue
        return StopUpdate(
            stop_loss=candidate,
            swing_low=float(swing.price),
            swing_timestamp=int(swing.timestamp or timestamps[swing.index]),
            breakeven_floor=breakeven,
            atr=atr,
        )
    return None
