"""Deterministic technical indicators for Nobitex candle data; no trade signals."""
from dataclasses import dataclass
from collections.abc import Sequence
from .indicators import sma, rsi


@dataclass(frozen=True)
class TechnicalSummary:
    trend: str
    latest_close: float
    sma_fast: float | None
    sma_slow: float | None
    rsi14: float | None
    candle_count: int
    notes: tuple[str, ...]


def analyze_closes(closes: Sequence[float], fast_period: int = 7,
                   slow_period: int = 25) -> TechnicalSummary:
    values = [float(v) for v in closes]
    if any(not (v > 0 and v < float("inf")) for v in values):
        raise ValueError("close prices must be finite and positive")
    if not values:
        raise ValueError("at least one close price is required")
    if fast_period <= 0 or slow_period <= fast_period:
        raise ValueError("periods must be positive and slow_period > fast_period")

    fast = sma(values, fast_period) if len(values) >= fast_period else None
    slow = sma(values, slow_period) if len(values) >= slow_period else None
    rsi_value = rsi(values, 14) if len(values) >= 15 else None
    notes = []
    if fast is None or slow is None:
        trend = "داده ناکافی"
        notes.append(f"برای مقایسهٔ میانگین‌ها حداقل {slow_period} کندل لازم است.")
    elif fast > slow:
        trend = "تمایل صعودی"
        notes.append("میانگین کوتاه‌مدت بالاتر از میانگین بلندمدت است؛ این به‌تنهایی سیگنال خرید نیست.")
    elif fast < slow:
        trend = "تمایل نزولی"
        notes.append("میانگین کوتاه‌مدت پایین‌تر از میانگین بلندمدت است؛ این به‌تنهایی سیگنال فروش نیست.")
    else:
        trend = "خنثی"
        notes.append("دو میانگین برابرند؛ جهت مشخصی از این معیار دیده نمی‌شود.")
    if rsi_value is None:
        notes.append("برای RSI دورهٔ ۱۴ حداقل ۱۵ قیمت بسته‌شدن لازم است.")
    return TechnicalSummary(trend, values[-1], fast, slow, rsi_value, len(values), tuple(notes))
