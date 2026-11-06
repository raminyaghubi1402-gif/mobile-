"""Educational OHLC candle analysis with explicit input validation."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Candle:
    open: float
    high: float
    low: float
    close: float

    def validate(self) -> None:
        values = (self.open, self.high, self.low, self.close)
        if any(value < 0 for value in values):
            raise ValueError("OHLC values must be non-negative")
        if self.high < self.low:
            raise ValueError("high must be >= low")
        if not (self.low <= self.open <= self.high):
            raise ValueError("open must be between low and high")
        if not (self.low <= self.close <= self.high):
            raise ValueError("close must be between low and high")

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low

    @property
    def direction(self) -> str:
        if self.close > self.open:
            return "صعودی"
        if self.close < self.open:
            return "نزولی"
        return "خنثی (دوجی/بدنه برابر)"


def analyze_candle(candle: Candle) -> dict[str, float | str]:
    candle.validate()
    return {
        "direction": candle.direction,
        "body": candle.body,
        "upper_wick": candle.upper_wick,
        "lower_wick": candle.lower_wick,
        "range": candle.high - candle.low,
        "notice": "تحلیل آموزشی یک کندل است؛ به‌تنهایی سیگنال معامله نیست.",
    }
