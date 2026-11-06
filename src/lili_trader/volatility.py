"""Volatility measurements for closed OHLC candles; no network or trading actions."""
from __future__ import annotations

from collections.abc import Sequence
from math import isfinite


class VolatilityError(ValueError):
    """Raised when OHLC input is incomplete or invalid."""


def _validate_candles(candles: Sequence[dict[str, float]]) -> None:
    if not candles:
        raise VolatilityError("حداقل یک کندل لازم است.")
    for index, candle in enumerate(candles):
        try:
            high = float(candle["high"])
            low = float(candle["low"])
            close = float(candle["close"])
        except (KeyError, TypeError, ValueError) as exc:
            raise VolatilityError(f"اطلاعات کندل شمارهٔ {index} ناقص است.") from exc
        if not all(isfinite(value) and value > 0 for value in (high, low, close)):
            raise VolatilityError(f"قیمت‌های کندل شمارهٔ {index} معتبر نیستند.")
        if high < low or close > high or close < low:
            raise VolatilityError(f"ساختار OHLC کندل شمارهٔ {index} نامعتبر است.")


def true_ranges(candles: Sequence[dict[str, float]]) -> list[float]:
    """Calculate True Range for each candle; first candle uses high-low."""
    _validate_candles(candles)
    ranges: list[float] = []
    previous_close: float | None = None
    for candle in candles:
        high, low, close = float(candle["high"]), float(candle["low"]), float(candle["close"])
        if previous_close is None:
            value = high - low
        else:
            value = max(high - low, abs(high - previous_close), abs(low - previous_close))
        ranges.append(value)
        previous_close = close
    return ranges


def atr(candles: Sequence[dict[str, float]], period: int = 14) -> float:
    """Wilder ATR over the latest period true ranges (simple seed, Wilder smoothing)."""
    if period <= 0:
        raise VolatilityError("دورهٔ ATR باید مثبت باشد.")
    ranges = true_ranges(candles)
    if len(ranges) < period:
        raise VolatilityError("تعداد کندل‌ها برای محاسبهٔ ATR کافی نیست.")
    value = sum(ranges[:period]) / period
    for current in ranges[period:]:
        value = ((period - 1) * value + current) / period
    return value


def atr_percent(candles: Sequence[dict[str, float]], period: int = 14) -> float:
    """ATR as a percentage of the latest candle close, useful across symbols."""
    value = atr(candles, period)
    close = float(candles[-1]["close"])
    return (value / close) * 100
