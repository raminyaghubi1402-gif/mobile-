"""Independent market-structure engine: confirmed swings, BOS and CHoCH.

Events use candle closes (not wick-only breaks) and confirmed pivots.
A pivot is confirmed only after pivot_window candles have closed to its right.
This module is descriptive/read-only; it does not create trade signals.
"""
from __future__ import annotations
from dataclasses import dataclass
from collections.abc import Mapping, Sequence
from math import isfinite

@dataclass(frozen=True)
class Swing:
    kind: str
    price: float
    index: int
    timestamp: int | None = None

@dataclass(frozen=True)
class StructureEvent:
    kind: str
    level: float
    candle_index: int
    timestamp: int | None
    broken_swing_index: int
    close: float
    description_fa: str

@dataclass(frozen=True)
class StructureAnalysis:
    bias: str
    last_event: StructureEvent | None
    events: tuple[StructureEvent, ...]
    swings: tuple[Swing, ...]
    latest_close: float | None
    reason_fa: str

def _value(candle, name):
    return candle.get(name) if isinstance(candle, Mapping) else getattr(candle, name, None)

def analyze_market_structure(candles: Sequence, *, pivot_window: int = 2) -> StructureAnalysis:
    """Detect BOS/CHoCH independently. Wick-only breaks are ignored; invalid data fails closed."""
    if pivot_window < 1:
        raise ValueError("pivot_window must be >= 1")
    if not isinstance(candles, Sequence) or isinstance(candles, (str, bytes)):
        raise TypeError("candles must be a sequence")
    if len(candles) < 2 * pivot_window + 3:
        return StructureAnalysis("unknown", None, (), (), None,
                                 "کندل کافی برای تأیید سوئینگ و شکست ساختار وجود ندارد.")
    rows, prev_ts = [], None
    for c in candles:
        try:
            o, h, l, cl = (float(_value(c, k)) for k in ("open", "high", "low", "close"))
        except (TypeError, ValueError, OverflowError):
            raise ValueError("OHLC must be numeric")
        if not all(isfinite(x) and x > 0 for x in (o, h, l, cl)) or h < max(o, cl, l) or l > min(o, cl):
            raise ValueError("invalid OHLC candle")
        ts = _value(c, "timestamp")
        if ts is not None:
            ts = int(ts)
            if prev_ts is not None and ts <= prev_ts:
                raise ValueError("timestamps must be strictly increasing")
            prev_ts = ts
        rows.append((o, h, l, cl, ts))
    swings = []
    for i in range(pivot_window, len(rows) - pivot_window):
        h, low = rows[i][1], rows[i][2]
        neighbors = rows[i-pivot_window:i] + rows[i+1:i+pivot_window+1]
        if all(h > x[1] for x in neighbors):
            swings.append(Swing("high", h, i, rows[i][4]))
        if all(low < x[2] for x in neighbors):
            swings.append(Swing("low", low, i, rows[i][4]))
    swings.sort(key=lambda s: (s.index, 0 if s.kind == "high" else 1))
    highs = [s for s in swings if s.kind == "high"]
    lows = [s for s in swings if s.kind == "low"]
    if len(highs) < 2 or len(lows) < 2:
        return StructureAnalysis("unknown", None, (), tuple(swings), rows[-1][3],
                                 "حداقل دو سقف و دو کف تأییدشده لازم است.")
    hh, hl = highs[-1].price > highs[-2].price, lows[-1].price > lows[-2].price
    lh, ll = highs[-1].price < highs[-2].price, lows[-1].price < lows[-2].price
    bias = "up" if hh and hl else "down" if lh and ll else "range"
    active_bias = bias
    protected_high, protected_low = highs[-1], lows[-1]
    events, broken = [], set()
    for i, row in enumerate(rows):
        close, ts = row[3], row[4]
        for s in swings:
            if s.index + pivot_window == i:
                if s.kind == "high": protected_high = s
                else: protected_low = s
        if active_bias in ("up", "range") and protected_high and close > protected_high.price:
            if protected_high.index not in broken:
                kind = "BOS_UP" if active_bias == "up" else "CHOCH_UP"
                fa = "شکست ساختار صعودی (BOS)" if kind == "BOS_UP" else "تغییر کاراکتر صعودی (CHoCH)"
                events.append(StructureEvent(kind, protected_high.price, i, ts, protected_high.index, close, fa))
                broken.add(protected_high.index)
                active_bias = "up"
        elif active_bias in ("down", "range") and protected_low and close < protected_low.price:
            if protected_low.index not in broken:
                kind = "BOS_DOWN" if active_bias == "down" else "CHOCH_DOWN"
                fa = "شکست ساختار نزولی (BOS)" if kind == "BOS_DOWN" else "تغییر کاراکتر نزولی (CHoCH)"
                events.append(StructureEvent(kind, protected_low.price, i, ts, protected_low.index, close, fa))
                broken.add(protected_low.index)
                active_bias = "down"
    latest = events[-1] if events else None
    reason = (f"آخرین رویداد: {latest.description_fa}؛ شکست با بسته‌شدن کندل تأیید شده است."
              if latest else "هیچ شکست معتبرِ سوئینگ با بسته‌شدن کندل ثبت نشده؛ عبور سایه شکست محسوب نمی‌شود.")
    return StructureAnalysis(active_bias, latest, tuple(events), tuple(swings), rows[-1][3], reason)
