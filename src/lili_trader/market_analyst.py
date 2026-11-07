"""Professional, explainable market-analysis orchestrator; research-only."""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping, Sequence

from .price_action_trend import classify_price_action_trend
from .support_resistance import find_support_resistance
from .timeframe_gate import evaluate_timeframe_gate, GateDecision
from .market_regime import classify_market_regime, select_strategy_candidates
from .market_structure import analyze_market_structure


@dataclass(frozen=True)
class FrameResult:
    timeframe: str
    trend: object
    zones: tuple
    structure: object | None = None


@dataclass(frozen=True)
class MarketAnalysis:
    symbol: str
    decision: str
    direction: str | None
    confidence: str
    frames: tuple[FrameResult, ...]
    regime: object | None
    candidates: tuple
    gate: object | None
    report_fa: str


_CONTEXT = ("1M", "1W", "1D", "4h")
_REQUIRED = ("1h", "15m", "5m")


def _closed(candles_by_timeframe, timeframe):
    rows = candles_by_timeframe.get(timeframe, ())
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        return ()
    # If a caller supplies closure metadata, trust only the explicit boolean
    # True. False, strings ("true"/"false"), null, and other ambiguous values
    # are not proof that the candle is closed. Rows without this optional field
    # remain supported for offline/synthetic analysis; production data is
    # filtered and freshness-checked by market_analysis_runner.
    return tuple(
        row for row in rows
        if not isinstance(row, Mapping)
        or "is_closed" not in row
        or row.get("is_closed") is True
    )


def _candle_value(candle, name):
    return candle.get(name) if isinstance(candle, Mapping) else getattr(candle, name, None)


def _touches_zone(candle, zones, kind):
    """True only when the latest closed candle range intersects a matching zone."""
    try:
        low = float(_candle_value(candle, "low"))
        high = float(_candle_value(candle, "high"))
    except (TypeError, ValueError, OverflowError):
        return False
    if low <= 0 or high < low:
        return False
    return any(
        zone.kind == kind and low <= zone.upper and high >= zone.lower
        for zone in zones
    )


def analyze_market(symbol: str, candles_by_timeframe: Mapping, *, pivot_window: int = 2) -> MarketAnalysis:
    """Analyze closed OHLC candles; missing data or unconfirmed setups mean WAIT."""
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("symbol must be non-empty")
    if not isinstance(candles_by_timeframe, Mapping):
        raise TypeError("candles_by_timeframe must be a mapping")

    frames = {}
    warnings = []
    for tf in (*_CONTEXT, *_REQUIRED):
        rows = _closed(candles_by_timeframe, tf)
        if not rows:
            if tf in _REQUIRED:
                warnings.append(f"داده بسته‌شدهٔ {tf} موجود نیست.")
            continue
        try:
            trend = classify_price_action_trend(rows, pivot_window=pivot_window)
        except (ValueError, TypeError, OverflowError):
            warnings.append(f"دادهٔ {tf} نامعتبر است؛ این بازه از تحلیل حذف شد.")
            continue
        try:
            zones = find_support_resistance(rows, pivot_window=max(2, pivot_window))
        except (ValueError, TypeError, OverflowError):
            zones = ()
            warnings.append(f"برای {tf} دادهٔ کافی جهت تعیین حمایت/مقاومت وجود ندارد.")
        try:
            structure = analyze_market_structure(rows, pivot_window=pivot_window)
        except (ValueError, TypeError, OverflowError):
            structure = None
            warnings.append(f"تحلیل BOS/CHoCH در {tf} به‌علت دادهٔ نامعتبر متوقف شد.")
        frames[tf] = FrameResult(tf, trend, zones, structure)

    missing = [tf for tf in _REQUIRED if tf not in frames]
    gate = regime = None
    candidates = ()
    direction = None
    confidence = "low"
    decision = "WAIT"
    zone_touched = False
    if not missing:
        hourly, fifteen, five = (frames[tf].trend for tf in _REQUIRED)
        rows_15m = _closed(candles_by_timeframe, "15m")
        rows_5m = _closed(candles_by_timeframe, "5m")
        latest_15m = rows_15m[-1]
        latest_5m = rows_5m[-1]
        previous_5m = rows_5m[-2] if len(rows_5m) >= 2 else None
        close_15m = _candle_value(latest_15m, "close")
        previous_5m_close = _candle_value(previous_5m, "close") if previous_5m else None
        latest_5m_close = _candle_value(latest_5m, "close")

        if hourly.direction == "up":
            zone_kind = "support"
        elif hourly.direction == "down":
            zone_kind = "resistance"
        else:
            zone_kind = None
        if zone_kind:
            zone_touched = (
                _touches_zone(latest_15m, frames["1h"].zones, zone_kind)
                or _touches_zone(latest_15m, frames["15m"].zones, zone_kind)
            )

        gate = evaluate_timeframe_gate(
            hourly, fifteen, five, close_15m,
            setup_zone_touched=zone_touched,
            previous_5m_close=previous_5m_close,
            latest_5m_close=latest_5m_close,
            five_minute_structure=frames["5m"].structure,
            five_minute_candle_count=len(rows_5m),
            require_structure_confirmation=True,
        )
        direction = gate.direction

        # Fail closed when independently computed structure disagrees with
        # the trend labels. 1h defines bias; 15m must agree with its own
        # price-action state (it may still be a countertrend pullback);
        # the 5m entry trigger is separately required to be fresh BOS/CHoCH.
        hourly_structure = frames["1h"].structure
        fifteen_structure = frames["15m"].structure
        structural_conflict = False
        structural_reason = ""
        if direction in ("up", "down"):
            if not hourly_structure or hourly_structure.bias != direction:
                structural_conflict = True
                structural_reason = (
                    "ساختار مستقل ۱ساعته نامشخص یا مخالف روند اصلی است؛ "
                    "تا تأیید BOS/CHoCH هم‌جهت ورود ممنوع."
                )
            elif (hourly_structure.last_event
                  and hourly_structure.last_event.kind == ("CHOCH_DOWN" if direction == "up" else "CHOCH_UP")):
                structural_conflict = True
                structural_reason = "CHoCH یک‌ساعته خلاف جهت ستاپ ثبت شده؛ ورود ممنوع."
            elif not fifteen_structure or fifteen_structure.bias != fifteen.direction:
                structural_conflict = True
                structural_reason = (
                    "ساختار مستقل ۱۵ دقیقه با تحلیل روند همان تایم‌فریم هم‌خوان نیست؛ "
                    "ستاپ تا رفع اختلاف متوقف است."
                )
        if structural_conflict:
            gate = GateDecision("structure_conflict", False, direction, structural_reason)
            warnings.append(gate.reason)

        try:
            regime = classify_market_regime(_closed(candles_by_timeframe, "1h"), hourly)
            candidates = select_strategy_candidates(regime)
        except (ValueError, TypeError, OverflowError):
            warnings.append("تشخیص رژیم بازار به‌علت دادهٔ ناکافی/نامعتبر متوقف شد.")
        if gate.allowed and not structural_conflict and regime and regime.trend == direction:
            decision = "READY_BUY" if direction == "up" else "READY_SELL"
            confidence = "moderate" if regime.confidence == "moderate" else "low"
        else:
            warnings.append(gate.reason)
            decision, confidence = "WAIT", "low"
    else:
        warnings.append("به‌علت فقدان تایم‌فریم ضروری، هیچ سناریوی ورودی ساخته نشد.")

    lines = [
        f"تحلیل حرفه‌ای بازار لی‌لی — {symbol}",
        f"تصمیم: {decision}",
        f"جهت مجاز: {'فقط BUY (فرضی)' if direction == 'up' else 'فقط SELL (فرضی)' if direction == 'down' else 'WAIT؛ بدون جهت مجاز'}",
        f"اعتماد کیفی: {confidence} (این عدد احتمال سود نیست)",
        "", "ساختار و نواحی قیمت:",
    ]
    for tf in (*_CONTEXT, *_REQUIRED):
        frame = frames.get(tf)
        if not frame:
            lines.append(f"- {tf}: دادهٔ معتبر موجود نیست.")
            continue
        t = frame.trend
        lines.append(f"- {tf}: {t.label_fa} — {t.reason}")
        if frame.structure:
            structure = frame.structure
            bias_fa = {"up": "صعودی", "down": "نزولی", "range": "رنج", "unknown": "نامشخص"}.get(
                structure.bias, structure.bias
            )
            event = structure.last_event
            if event:
                lines.append(
                    f"  ساختار مستقل: {bias_fa}؛ {event.kind} در کندل {event.candle_index} "
                    f"روی سطح {event.level:.8g} — {event.description_fa}"
                )
            else:
                lines.append(f"  ساختار مستقل: {bias_fa}؛ BOS/CHoCH تأییدشده‌ای ثبت نشده.")
        else:
            lines.append("  ساختار مستقل BOS/CHoCH: قابل محاسبه نیست.")
        if t.direction in ("up", "down"):
            kind = "support" if t.direction == "up" else "resistance"
            relevant = [zone for zone in frame.zones if zone.kind == kind]
            if relevant:
                nearest = min(relevant, key=lambda zone: abs(zone.midpoint - t.latest_close))
                lines.append(f"  {'حمایت' if kind == 'support' else 'مقاومت'}: {nearest.lower:.8g} تا {nearest.upper:.8g}؛ {nearest.touches} برخورد")
    if regime:
        lines.extend(("", f"رژیم: {regime.trend}؛ نوسان: {regime.volatility}؛ {regime.reason}",
                      "استراتژی‌های قابل بررسی (نه سیگنال):"))
        lines.extend(f"- {item.name}: {item.reason}" for item in candidates)
    lines.extend(("", "کنترل ورود ۱h/۱۵m/۵m:",
                  gate.reason if gate else "به‌دلیل دادهٔ ناقص اجرا نشد.",
                  f"برخورد کندل ۱۵ دقیقه با ناحیه {'تأیید شد' if zone_touched else 'تأیید نشد'}." if gate else ""))
    if warnings:
        lines.extend(("", "هشدارها:"))
        lines.extend(f"- {warning}" for warning in warnings)
    lines.extend(("", "فقط تحلیل و سناریوی فرضی؛ توصیهٔ مالی نیست و هیچ معامله‌ای اجرا نمی‌شود."))
    return MarketAnalysis(symbol, decision, direction, confidence,
                          tuple(frames[tf] for tf in (*_CONTEXT, *_REQUIRED) if tf in frames),
                          regime, tuple(candidates), gate, "\n".join(lines))
