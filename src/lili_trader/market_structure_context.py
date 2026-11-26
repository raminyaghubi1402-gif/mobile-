"""Conservative market-structure context: hierarchy, labels, range and sweeps.

This layer is descriptive only. It does not issue trade signals; all pivots
come from the confirmed-swing engine and are not usable before confirmation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from .market_structure import analyze_market_structure


@dataclass(frozen=True)
class SwingLabel:
    kind: str
    price: float
    index: int
    label: str
    hierarchy: str
    confirmation_index: int


@dataclass(frozen=True)
class LiquiditySweep:
    kind: str  # SWEEP_HIGH / SWEEP_LOW
    level: float
    swing_index: int
    candle_index: int
    timestamp: int | None
    close: float


@dataclass(frozen=True)
class RangeContext:
    status: str  # unknown / candidate / range / breakout_up / breakout_down
    high: float | None
    low: float | None
    width: float | None
    width_pct: float | None
    touches_high: int
    touches_low: int


@dataclass(frozen=True)
class MarketContext:
    bias: str
    labels: tuple[SwingLabel, ...]
    internal_swings: tuple[SwingLabel, ...]
    external_swings: tuple[SwingLabel, ...]
    protected_high: SwingLabel | None
    protected_low: SwingLabel | None
    sweeps: tuple[LiquiditySweep, ...]
    range_context: RangeContext
    core_analysis: object
    reason_fa: str


def _candle_value(candle, key):
    return candle.get(key) if isinstance(candle, Mapping) else getattr(candle, key, None)


def _confirmation(swing, window):
    value = getattr(swing, "confirmation_index", None)
    return value if value is not None else swing.index + window


def _label_swings(swings, hierarchy, window):
    prior_high = prior_low = None
    result = []
    for swing in sorted(swings, key=lambda s: (s.index, s.kind)):
        confirmation = _confirmation(swing, window)
        if swing.kind == "high":
            label = "SH" if prior_high is None else (
                "HH" if swing.price > prior_high.price else
                "LH" if swing.price < prior_high.price else "EQH"
            )
            prior_high = swing
        else:
            label = "SL" if prior_low is None else (
                "HL" if swing.price > prior_low.price else
                "LL" if swing.price < prior_low.price else "EQL"
            )
            prior_low = swing
        result.append(SwingLabel(swing.kind, swing.price, swing.index, label,
                                 hierarchy, confirmation))
    return result


def analyze_market_context(candles: Sequence, *, pivot_window: int = 2,
                           external_window: int | None = None) -> MarketContext:
    """Return internal/external swing labels, protection, sweeps and range state.

    External pivots use a wider fractal window (default pivot_window + 1).
    A candidate range is promoted only when the core bias is neutral/range and
    both boundaries have repeated near-touches. Boundary breaks are close-based.
    """
    if type(pivot_window) is not int or pivot_window < 1:
        raise ValueError("pivot_window must be a positive integer")
    if external_window is None:
        external_window = pivot_window + 1
    if type(external_window) is not int or external_window <= pivot_window:
        raise ValueError("external_window must be an integer larger than pivot_window")

    core = analyze_market_structure(candles, pivot_window=pivot_window)
    ext_core = analyze_market_structure(candles, pivot_window=external_window)
    internal_labels = _label_swings(core.swings, "internal", pivot_window)
    external_labels = _label_swings(ext_core.swings, "external", external_window)
    external_keys = {(s.kind, s.index) for s in ext_core.swings}
    external_label_by_key = {(s.kind, s.index): s for s in external_labels}
    all_labels = tuple(
        external_label_by_key.get((s.kind, s.index), s)
        if (s.kind, s.index) in external_keys else s
        for s in internal_labels
    )
    internal_only = tuple(s for s in all_labels if s.hierarchy == "internal")
    external_only = tuple(external_labels)

    # Latest BOS identifies the opposing swing that was confirmed by the break.
    protected_high = protected_low = None
    for event in core.events:
        # Protection is based on the internal confirmed swing chronology; a
        # wider-window external classification must not delay this level.
        eligible = [s for s in internal_labels if s.confirmation_index <= event.candle_index]
        if event.kind == "BOS_UP":
            lows = [s for s in eligible if s.kind == "low"]
            if lows:
                protected_low = lows[-1]
        elif event.kind == "BOS_DOWN":
            highs = [s for s in eligible if s.kind == "high"]
            if highs:
                protected_high = highs[-1]

    sweeps, seen = [], set()
    rows = list(candles)
    for swing in core.swings:
        key = (swing.kind, swing.index)
        if key in seen:
            continue
        seen.add(key)
        start = _confirmation(swing, pivot_window) + 1
        for i in range(start, len(rows)):
            high = float(_candle_value(rows[i], "high"))
            low = float(_candle_value(rows[i], "low"))
            close = float(_candle_value(rows[i], "close"))
            ts = _candle_value(rows[i], "timestamp")
            if swing.kind == "high" and high > swing.price and close <= swing.price:
                sweeps.append(LiquiditySweep("SWEEP_HIGH", swing.price, swing.index, i, ts, close))
                break
            if swing.kind == "low" and low < swing.price and close >= swing.price:
                sweeps.append(LiquiditySweep("SWEEP_LOW", swing.price, swing.index, i, ts, close))
                break

    # Boundaries use the latest three external pivots on each side; near-touch
    # tolerance avoids requiring an exact price print to count as a test.
    recent_high_swings = [s for s in ext_core.swings if s.kind == "high"][-3:]
    recent_low_swings = [s for s in ext_core.swings if s.kind == "low"][-3:]
    upper_swing = max(recent_high_swings, key=lambda s: s.price, default=None)
    lower_swing = min(recent_low_swings, key=lambda s: s.price, default=None)
    upper = upper_swing.price if upper_swing else None
    lower = lower_swing.price if lower_swing else None
    latest_close = core.latest_close
    tolerance = 0.0025
    # A range boundary is not considered available before both selected
    # external pivots have been confirmed. Count touches only after that point.
    boundary_start = max(
        _confirmation(upper_swing, external_window) if upper_swing else 0,
        _confirmation(lower_swing, external_window) if lower_swing else 0,
    )
    boundary_rows = rows[boundary_start:]
    touches_high = sum(1 for row in boundary_rows if upper is not None and
                       float(_candle_value(row, "high")) >= upper * (1 - tolerance) and
                       float(_candle_value(row, "close")) <= upper * (1 + tolerance))
    touches_low = sum(1 for row in boundary_rows if lower is not None and
                      float(_candle_value(row, "low")) <= lower * (1 + tolerance) and
                      float(_candle_value(row, "close")) >= lower * (1 - tolerance))
    width = upper - lower if upper is not None and lower is not None and upper > lower else None
    width_pct = (100 * width / ((upper + lower) / 2)) if width is not None and upper + lower else None
    if upper is None or lower is None or latest_close is None or width is None:
        status = "unknown"
    elif latest_close > upper:
        status = "breakout_up"
    elif latest_close < lower:
        status = "breakout_down"
    elif core.bias in ("range", "unknown"):
        status = "range" if touches_high >= 2 and touches_low >= 2 else "candidate"
    else:
        status = "candidate"
    range_context = RangeContext(status, upper, lower, width, width_pct,
                                touches_high, touches_low)

    explanation = {
        "range": "ساختار خنثی با برخوردهای تکراری به محدوده؛ رنج محتمل است.",
        "candidate": "محدودهٔ بالقوه دیده می‌شود، اما تأیید رنج کافی نیست.",
        "breakout_up": "کلوز بالاتر از سقف خارجی ثبت شده؛ شکست محدوده، نه تأیید خودکار روند.",
        "breakout_down": "کلوز پایین‌تر از کف خارجی ثبت شده؛ شکست محدوده، نه تأیید خودکار روند.",
        "unknown": "برای تعیین محدودهٔ خارجی دادهٔ تأییدشده کافی نیست.",
    }[status]
    if sweeps:
        explanation += f" {len(sweeps)} جاروب نقدینگی (wick + close-back) ثبت شده است."
    return MarketContext(core.bias, all_labels, internal_only, external_only,
                         protected_high, protected_low, tuple(sweeps), range_context,
                         core, explanation)


@dataclass(frozen=True)
class TimeframeAlignment:
    biases: tuple[tuple[str, str], ...]
    consensus: str
    higher_timeframe_bias: str
    aligned: tuple[str, ...]
    conflicting: tuple[str, ...]
    reason_fa: str


def analyze_timeframe_alignment(timeframes: Mapping[str, Sequence], *,
                                pivot_window: int = 2) -> TimeframeAlignment:
    """Compare timeframes independently; higher-TF neutrality is not a mandate."""
    order = {"1d": 0, "d": 0, "daily": 0, "4h": 1, "h4": 1,
             "1h": 2, "h1": 2, "15m": 3, "m15": 3, "5m": 4, "m5": 4,
             "1m": 5, "m1": 5}
    keys = sorted(timeframes, key=lambda k: (order.get(str(k).lower(), 99), str(k)))
    analyses = [(str(key), analyze_market_structure(timeframes[key], pivot_window=pivot_window).bias)
                for key in keys]
    directional = [bias for _, bias in analyses if bias in ("up", "down")]
    higher = analyses[0][1] if analyses else "unknown"
    if not directional:
        consensus = "neutral"
    elif len(directional) == len(analyses) and all(b == directional[0] for b in directional):
        consensus = directional[0]
    else:
        consensus = "mixed"
    aligned = tuple(k for k, b in analyses if higher in ("up", "down") and b == higher)
    conflicting = tuple(k for k, b in analyses if higher in ("up", "down") and b in ("up", "down") and b != higher)
    if higher not in ("up", "down"):
        reason = "تایم‌فریم بالاتر جهت قطعی ندارد؛ از تایم‌فریم پایین‌تر جهت‌گیری اجباری نکن."
    elif conflicting:
        reason = f"تعارض با جهت تایم‌فریم بالاتر ({higher}) در: {', '.join(conflicting)}."
    else:
        reason = f"جهت تایم‌فریم بالاتر {higher} است؛ تایم‌فریم‌های هم‌جهت: {', '.join(aligned) or 'هیچ'}."
    return TimeframeAlignment(tuple(analyses), consensus, higher, aligned, conflicting, reason)
