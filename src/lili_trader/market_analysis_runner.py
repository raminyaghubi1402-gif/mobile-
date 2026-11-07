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
    for timeframe, resolution in resolutions.items():
        fetched = adapter.fetch_history(
            symbol=symbol, resolution=resolution, count=count
        )
        candles[timeframe] = _closed_only(fetched, timeframe)
    result = analyze_market(symbol, candles)
    return result.report_fa
