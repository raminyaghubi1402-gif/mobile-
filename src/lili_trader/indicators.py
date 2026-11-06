"""Simple deterministic indicators. No network calls and no trading actions."""
from collections.abc import Sequence


def sma(values: Sequence[float], period: int) -> float:
    """Return simple moving average of the last period values."""
    if period <= 0:
        raise ValueError("period must be positive")
    if len(values) < period:
        raise ValueError("not enough values for period")
    return sum(values[-period:]) / period


def ema(values: Sequence[float], period: int) -> float:
    """Return EMA over the supplied sequence, seeded with the first value."""
    if period <= 0:
        raise ValueError("period must be positive")
    if not values:
        raise ValueError("values must not be empty")
    alpha = 2 / (period + 1)
    result = float(values[0])
    for value in values[1:]:
        result = alpha * float(value) + (1 - alpha) * result
    return result


def rsi(values: Sequence[float], period: int = 14) -> float:
    """Compute a basic RSI using average gains/losses over the last period."""
    if period <= 0:
        raise ValueError("period must be positive")
    if len(values) < period + 1:
        raise ValueError("not enough values for RSI period")
    changes = [float(values[i]) - float(values[i - 1]) for i in range(len(values) - period, len(values))]
    gains = sum(change for change in changes if change > 0) / period
    losses = sum(-change for change in changes if change < 0) / period
    if losses == 0:
        return 100.0 if gains > 0 else 50.0
    relative_strength = gains / losses
    return 100 - (100 / (1 + relative_strength))
