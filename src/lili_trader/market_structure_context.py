"""Conservative market-structure context: hierarchy, labels, range and sweeps.

This layer is deliberately descriptive. It does not issue trade signals, and all
pivots are taken from the confirmed-swing engine (never future-known pivots).
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
    """Build labeled internal/external structure, sweeps and range context.

    External pivots use a wider fractal window (default pivot_window + 1).
    A range is only promoted from candidate when the core bias is neutral/range;
    close-based boundary breaks are kept distinct from wick sweeps.
    """
    if type(pivot_window) is not int or pivot_window < 1:
        raise ValueError("pivot_window must be a positive integer")
    if external_window is None:
        external_window = pivot_window + 1
    if type(external_window) is not int or external_window <= pivot_window:
        raise ValueError("external_window must be an integer larger than pivot_window")

    core = analyze_market_structure(candles, pivot_window=pivot_window)
    ext_core = analyze_market_structure(candles, pivot_window=external_window)
    internal = _label_swings(core.swings, "internal", pivot_window)
    external = _label_swings(ext_core.swings, "external", external_window)
    external_keys = {(s.kind, s.index) for s in ext_core.swings}
    all_labels = tuple(
        SwingLabel(s.kind, s.price, s.index, s.label,
                  "external" if (s.kind, s.index) in external_keys else "internal",
                  s.confirmation_index)
        for s in internal
    )
    internal_only = tuple(s for s in all_labels if s.hierarchy == "internal")
    external_only = tuple(external)

    # The most recent BOS determines the protected opposite-side swing.
    protected_high = protected_low = None
    for event in core.events:
        eligible = [s for s in all_labels if s.confirmation_index <= event.candle_index]
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

    ext_highs = [s for s in ext_core.swings if s.kind == "high"]
    ext_lows = [s for s in ext_core.swings if s.kind == "low"]
    upper = max((s.price for s in ext_highs), default=None)
    lower = min((s.price for s in ext_lows), default=None)
    latest_close = core.latest_close
    touches_high = sum(1 for row in rows if upper is not None and float(_candle_value(row, "high")) >= upper)
    touches_low = sum(1 for row in rows if lower is not None and float(_candle_value(row, "low")) <= lower)
    width = upper - lower if upper is not None and lower is not None and upper > lower else None
    width_pct = (100 * width / ((upper + lower) / 2)) if width is not None and upper + lower else None
    if upper is None or lower is None or latest_close is None:
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
    """Analyze each timeframe independently and compare against the highest one.

    Conventional key order is 1d, 4h, 1h, 15m, 5m, 1m; unknown keys follow.
    A neutral/unknown higher timeframe is not treated as a directional mandate.
    """
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
    elif all(b == directional[0] for b in directional) and len(directional) == len(analyses):
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
