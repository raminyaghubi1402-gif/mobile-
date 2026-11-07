"""Independent market-structure engine: confirmed swings, BOS and CHoCH.

Swings become available only after right-side confirmation. Break events use
candle closes, not wick-only breaches. The chronological loop avoids look-ahead.
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
            o, h, low, cl = (float(_value(c, k)) for k in ("open", "high", "low", "close"))
        except (TypeError, ValueError, OverflowError):
            raise ValueError("OHLC must be numeric")
        if not all(isfinite(x) and x > 0 for x in (o, h, low, cl)) or h < max(o, cl, low) or low > min(o, cl):
            raise ValueError("invalid OHLC candle")
        ts = _value(c, "timestamp")
        if ts is not None:
            try:
                ts = int(ts)
            except (TypeError, ValueError, OverflowError):
                raise ValueError("timestamp must be an integer")
            if prev_ts is not None and ts <= prev_ts:
                raise ValueError("timestamps must be strictly increasing")
            prev_ts = ts
        rows.append((o, h, low, cl, ts))

    all_swings = []
    pivots_by_confirmation = {}
    for p in range(pivot_window, len(rows) - pivot_window):
        neighbors = rows[p-pivot_window:p] + rows[p+1:p+pivot_window+1]
        if all(rows[p][1] > x[1] for x in neighbors):
            s = Swing("high", rows[p][1], p, rows[p][4])
            all_swings.append(s)
            pivots_by_confirmation.setdefault(p + pivot_window, []).append(s)
        if all(rows[p][2] < x[2] for x in neighbors):
            s = Swing("low", rows[p][2], p, rows[p][4])
            all_swings.append(s)
            pivots_by_confirmation.setdefault(p + pivot_window, []).append(s)
    all_swings.sort(key=lambda s: (s.index, s.kind))

    confirmed_highs, confirmed_lows = [], []
    protected_high = protected_low = None
    active_bias = "unknown"
    events, broken = [], set()

    for i, row in enumerate(rows):
        for s in pivots_by_confirmation.get(i, ()):
            (confirmed_highs if s.kind == "high" else confirmed_lows).append(s)

        if len(confirmed_highs) >= 2 and len(confirmed_lows) >= 2:
            h1, h2 = confirmed_highs[-2:]
            l1, l2 = confirmed_lows[-2:]
            highs_higher = h2.price > h1.price
            highs_lower = h2.price < h1.price
            lows_higher = l2.price > l1.price
            lows_lower = l2.price < l1.price
            if highs_higher and lows_higher:
                active_bias = "up"
            elif highs_lower and lows_lower:
                active_bias = "down"
            else:
                # Mixed swing progression is not evidence of a directional trend.
                # Do not carry an old trend bias through a structurally mixed phase.
                active_bias = "range"

        latest_high = confirmed_highs[-1] if confirmed_highs else None
        latest_low = confirmed_lows[-1] if confirmed_lows else None
        if active_bias == "up":
            protected_high = latest_high or protected_high
            # Keep the last protected HL until a new BOS confirms the next
            # structural leg. A merely confirmed minor HL is not enough.
            if protected_low is None and latest_low:
                protected_low = latest_low
        elif active_bias == "down":
            protected_low = latest_low or protected_low
            # Symmetric rule: keep the protected LH until a new bearish BOS.
            if protected_high is None and latest_high:
                protected_high = latest_high
        else:
            protected_high = latest_high or protected_high
            protected_low = latest_low or protected_low

        close, ts = row[3], row[4]
        if protected_high and close > protected_high.price and protected_high.index not in broken:
            kind = "BOS_UP" if active_bias == "up" else "CHOCH_UP" if active_bias == "down" else "BREAK_UP"
            fa = {"BOS_UP":"شکست ساختار صعودی (BOS)",
                  "CHOCH_UP":"تغییر کاراکتر صعودی (CHoCH)",
                  "BREAK_UP":"شکست سقف؛ جهت قبلی هنوز قطعی نیست"}[kind]
            events.append(StructureEvent(kind, protected_high.price, i, ts, protected_high.index, close, fa))
            broken.add(protected_high.index)
            # Neutral range breaks are events, not trend confirmation.
            if kind != "BREAK_UP":
                active_bias = "up"
            if confirmed_lows:
                protected_low = confirmed_lows[-1]
        elif protected_low and close < protected_low.price and protected_low.index not in broken:
            kind = "BOS_DOWN" if active_bias == "down" else "CHOCH_DOWN" if active_bias == "up" else "BREAK_DOWN"
            fa = {"BOS_DOWN":"شکست ساختار نزولی (BOS)",
                  "CHOCH_DOWN":"تغییر کاراکتر نزولی (CHoCH)",
                  "BREAK_DOWN":"شکست کف؛ جهت قبلی هنوز قطعی نیست"}[kind]
            events.append(StructureEvent(kind, protected_low.price, i, ts, protected_low.index, close, fa))
            broken.add(protected_low.index)
            # Neutral range breaks are events, not trend confirmation.
            if kind != "BREAK_DOWN":
                active_bias = "down"
            if confirmed_highs:
                protected_high = confirmed_highs[-1]

    latest = events[-1] if events else None
    reason = (f"آخرین رویداد: {latest.description_fa}؛ شکست با بسته‌شدن کندل تأیید شده است."
              if latest else "شکست معتبر سوئینگ با بسته‌شدن کندل ثبت نشده؛ عبور سایه شکست محسوب نمی‌شود.")
    return StructureAnalysis(active_bias, latest, tuple(events), tuple(all_swings), rows[-1][3], reason)
