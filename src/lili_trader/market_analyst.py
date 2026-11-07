"""Professional, explainable market-analysis orchestrator; research-only."""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping, Sequence

from .price_action_trend import classify_price_action_trend
from .support_resistance import find_support_resistance, nearest_zone
from .timeframe_gate import evaluate_timeframe_gate
from .market_regime import classify_market_regime, select_strategy_candidates


@dataclass(frozen=True)
class FrameResult:
    timeframe: str
    trend: object
    zones: tuple


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
    return tuple(row for row in rows if not (
        isinstance(row, Mapping) and row.get("is_closed") is False
    ))


def analyze_market(symbol: str, candles_by_timeframe: Mapping, *, pivot_window: int = 2) -> MarketAnalysis:
    """Analyze supplied closed OHLC candles; missing required TFs always mean WAIT."""
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
        frames[tf] = FrameResult(tf, trend, zones)

    missing = [tf for tf in _REQUIRED if tf not in frames]
    gate = regime = None
    candidates = ()
    direction = None
    confidence = "low"
    decision = "WAIT"
    if not missing:
        hourly, fifteen, five = (frames[tf].trend for tf in _REQUIRED)
        latest = _closed(candles_by_timeframe, "15m")[-1]
        close = latest.get("close") if isinstance(latest, Mapping) else getattr(latest, "close", None)
        gate = evaluate_timeframe_gate(hourly, fifteen, five, close)
        direction = gate.direction
        try:
            regime = classify_market_regime(_closed(candles_by_timeframe, "1h"), hourly)
            candidates = select_strategy_candidates(regime)
        except (ValueError, TypeError, OverflowError):
            warnings.append("تشخیص رژیم بازار به‌علت دادهٔ ناکافی/نامعتبر متوقف شد.")
        if gate.allowed and regime and regime.trend == direction:
            decision = "READY_BUY" if direction == "up" else "READY_SELL"
            confidence = "moderate" if gate.status == "ready" and regime.confidence == "moderate" else "low"
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
        support = nearest_zone(frame.zones, t.latest_close, "support")
        resistance = nearest_zone(frame.zones, t.latest_close, "resistance")
        if support:
            lines.append(f"  حمایت: {support.lower:.8g} تا {support.upper:.8g}؛ {support.touches} برخورد")
        if resistance:
            lines.append(f"  مقاومت: {resistance.lower:.8g} تا {resistance.upper:.8g}؛ {resistance.touches} برخورد")
    if regime:
        lines.extend(("", f"رژیم: {regime.trend}؛ نوسان: {regime.volatility}؛ {regime.reason}",
                      "استراتژی‌های قابل بررسی (نه سیگنال):"))
        lines.extend(f"- {item.name}: {item.reason}" for item in candidates)
    lines.extend(("", "کنترل ورود ۱h/۱۵m/۵m:", gate.reason if gate else "به‌دلیل دادهٔ ناقص اجرا نشد."))
    if warnings:
        lines.extend(("", "هشدارها:"))
        lines.extend(f"- {warning}" for warning in warnings)
    lines.extend(("", "فقط تحلیل و سناریوی فرضی؛ توصیهٔ مالی نیست و هیچ معامله‌ای اجرا نمی‌شود."))
    return MarketAnalysis(symbol, decision, direction, confidence,
                          tuple(frames[tf] for tf in (*_CONTEXT, *_REQUIRED) if tf in frames),
                          regime, tuple(candidates), gate, "\n".join(lines))
