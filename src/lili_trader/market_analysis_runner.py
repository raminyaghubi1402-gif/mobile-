"""Run the multi-timeframe analyst on fresh public Nobitex candles (read-only)."""
from __future__ import annotations

from .market_analyst import analyze_market
from .nobitex_data import NobitexAdapter


_INTERVAL_SECONDS = {
    "1h": 60 * 60,
    "15m": 15 * 60,
    "5m": 5 * 60,
    "4h": 4 * 60 * 60,
    "1D": 24 * 60 * 60,
}


def _closed_only(candles, timeframe):
    """Drop a candle whose open time + timeframe duration is after fetch time.

    Nobitex's UDF history response includes the currently-forming candle. Its
    timestamp is the candle start, while fetched_at is captured by the adapter.
    """
    duration = _INTERVAL_SECONDS[timeframe]
    result = []
    for candle in candles:
        def field(name):
            return candle.get(name) if isinstance(candle, dict) else getattr(candle, name, None)

        timestamp, fetched_at = field("timestamp"), field("fetched_at")
        # Keep custom candle objects without fetch metadata; the production
        # Nobitex adapter always supplies both fields.
        if timestamp is None or fetched_at is None:
            result.append(candle)
            continue
        try:
            is_closed = float(timestamp) + duration <= float(fetched_at)
        except (TypeError, ValueError, OverflowError):
            is_closed = False
        if is_closed:
            result.append(candle)
    return result


def _has_interval_gaps(candles, timeframe):
    """Require complete, strictly spaced OHLC history for production analysis."""
    if not candles:
        return True
    duration = _INTERVAL_SECONDS[timeframe]
    timestamps = []
    for candle in candles:
        raw = candle.get("timestamp") if isinstance(candle, dict) else getattr(candle, "timestamp", None)
        if raw is None or isinstance(raw, bool):
            return True
        try:
            numeric = float(raw)
        except (TypeError, ValueError, OverflowError):
            return True
        if numeric < 0 or numeric != numeric or numeric == float("inf") or not numeric.is_integer():
            return True
        timestamps.append(int(numeric))
    return any(
        current - previous != duration
        for previous, current in zip(timestamps, timestamps[1:])
    )


def _is_fresh_closed_history(candles, timeframe, *, max_stale_intervals=3):
    """Reject stale public data based on the newest closed candle's end time."""
    if not candles:
        return False
    duration = _INTERVAL_SECONDS[timeframe]
    latest = max(candles, key=lambda candle: float(
        candle.get("timestamp") if isinstance(candle, dict) else candle.timestamp
    ))
    def field(name):
        return latest.get(name) if isinstance(latest, dict) else getattr(latest, name, None)
    timestamp, fetched_at = field("timestamp"), field("fetched_at")
    if timestamp is None or fetched_at is None:
        # This helper is used for production API data; absent metadata cannot
        # establish freshness, so do not silently trust it.
        return False
    try:
        age_after_close = float(fetched_at) - (float(timestamp) + duration)
    except (TypeError, ValueError, OverflowError):
        return False
    return age_after_close <= duration * max_stale_intervals and age_after_close >= -duration


def fetch_and_analyze(symbol: str = "BTCIRT", *, count: int = 500) -> str:
    """Fetch closed 1h/15m/5m and context candles; return a Persian report.

    Weekly/monthly context is intentionally not synthesized in this first version.
    The analyst will state unavailable context rather than fabricate it.
    """
    adapter = NobitexAdapter()
    resolutions = {
        "1h": "60",
        "15m": "15",
        "5m": "5",
        "4h": "240",
        "1D": "D",
    }
    candles = {}
    stale_timeframes = []
    invalid_history_timeframes = []
    for timeframe, resolution in resolutions.items():
        fetched = adapter.fetch_history(
            symbol=symbol, resolution=resolution, count=count
        )
        closed = _closed_only(fetched, timeframe)
        if closed and not _is_fresh_closed_history(closed, timeframe):
            stale_timeframes.append(timeframe)
            closed = []
        elif closed and _has_interval_gaps(closed, timeframe):
            invalid_history_timeframes.append(timeframe)
            closed = []
        candles[timeframe] = closed
    result = analyze_market(symbol, candles)
    report = result.report_fa
    if stale_timeframes:
        labels = "، ".join(stale_timeframes)
        report += (
            "\\n\\nهشدار تازگی داده: آخرین کندل بسته‌شده در تایم‌فریم "
            + labels
            + " بیش از حد مجاز قدیمی است یا زمان آن قابل‌تأیید نیست؛ "
            "این تایم‌فریم‌ها از تحلیل حذف شدند و ورود مجاز نیست."
        )
    if invalid_history_timeframes:
        labels = "، ".join(invalid_history_timeframes)
        report += (
            "\\n\\nهشدار پیوستگی تاریخچه: در تایم‌فریم "
            + labels
            + " فاصله زمانی، ترتیب یا زمان کندل‌ها با بازه مورد انتظار سازگار نیست؛ "
            "این تاریخچه‌ها از تحلیل حذف شدند و ورود مجاز نیست."
        )
    return report
