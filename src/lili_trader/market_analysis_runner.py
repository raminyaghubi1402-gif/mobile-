"""Run the multi-timeframe analyst on fresh public Nobitex candles (read-only)."""
from __future__ import annotations

from .market_analyst import analyze_market
from .nobitex_data import NobitexAdapter


def fetch_and_analyze(symbol: str = "BTCIRT", *, count: int = 500) -> str:
    """Fetch 1h/15m/5m and context candles, then return a Persian analyst report.

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
        candles[timeframe] = adapter.fetch_history(
            symbol=symbol, resolution=resolution, count=count
        )
    result = analyze_market(symbol, candles)
    return result.report_fa
