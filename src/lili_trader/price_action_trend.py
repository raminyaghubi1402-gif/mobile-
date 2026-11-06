"""Price-action market regime from confirmed swing structure; no indicators."""
from __future__ import annotations
from dataclasses import dataclass
from math import isfinite
from collections.abc import Sequence

@dataclass(frozen=True)
class TrendState:
    direction: str
    label_fa: str
    latest_close: float
    last_two_swing_highs: tuple[float, float] | None
    last_two_swing_lows: tuple[float, float] | None
    pivot_count_high: int
    pivot_count_low: int
    reason: str

def classify_price_action_trend(candles: Sequence, *, pivot_window: int = 2, min_swings: int = 2) -> TrendState:
    """Use confirmed HH+HL / LH+LL structure; no technical indicators."""
    if pivot_window < 1 or min_swings < 2:
        raise ValueError("invalid swing parameters")
    if len(candles) < 2 * pivot_window + 2 * min_swings + 1:
        return TrendState("unknown", "داده ناکافی", 0.0, None, None, 0, 0,
                          "برای تشخیص ساختار بازار کندل کافی وجود ندارد.")
    rows = []
    previous_timestamp = None
    for candle in candles:
        def get(name):
            raw = candle.get(name) if isinstance(candle, dict) else getattr(candle, name)
            value = float(raw)
            if not isfinite(value):
                raise ValueError("OHLC values must be finite")
            return value
        high, low, open_, close = get("high"), get("low"), get("open"), get("close")
        timestamp = candle.get("timestamp") if isinstance(candle, dict) else getattr(candle, "timestamp", None)
        if low <= 0 or high < low or high < max(open_, close) or low > min(open_, close):
            raise ValueError("invalid OHLC candle")
        if timestamp is not None:
            timestamp = int(timestamp)
            if previous_timestamp is not None and timestamp <= previous_timestamp:
                raise ValueError("candles must be strictly chronological")
            previous_timestamp = timestamp
        rows.append((high, low, close))
    highs, lows = [], []
    for i in range(pivot_window, len(rows) - pivot_window):
        h, low, _ = rows[i]
        neighbors = rows[i-pivot_window:i] + rows[i+1:i+pivot_window+1]
        if all(h > item[0] for item in neighbors):
            highs.append(h)
        if all(low < item[1] for item in neighbors):
            lows.append(low)
    last_highs = tuple(highs[-2:]) if len(highs) >= min_swings else None
    last_lows = tuple(lows[-2:]) if len(lows) >= min_swings else None
    latest = rows[-1][2]
    if last_highs is None or last_lows is None:
        return TrendState("unknown", "داده ناکافی", latest, last_highs, last_lows,
                          len(highs), len(lows), "برای مقایسه دو سقف و دو کف تأییدشده کافی نیست.")
    higher_high, higher_low = last_highs[1] > last_highs[0], last_lows[1] > last_lows[0]
    lower_high, lower_low = last_highs[1] < last_highs[0], last_lows[1] < last_lows[0]
    if higher_high and higher_low:
        return TrendState("up", "صعودی", latest, last_highs, last_lows, len(highs), len(lows),
                          "سقف بالاتر (HH) و کف بالاتر (HL) تأیید شده‌اند.")
    if lower_high and lower_low:
        return TrendState("down", "نزولی", latest, last_highs, last_lows, len(highs), len(lows),
                          "سقف پایین‌تر (LH) و کف پایین‌تر (LL) تأیید شده‌اند.")
    return TrendState("range", "رنج / تغییرساختار", latest, last_highs, last_lows, len(highs), len(lows),
                      "ساختار سقف‌ها و کف‌ها هم‌جهت نیست؛ ورود در این وضعیت ممنوع است.")
