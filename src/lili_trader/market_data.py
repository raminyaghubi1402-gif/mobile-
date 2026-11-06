"""Market data adapters. Public Binance spot endpoint; no trading endpoints.

Forex support is deliberately an interface placeholder until a suitable data
provider and its terms/quotas are selected. Never present sample data as live.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class MarketCandle:
    symbol: str
    interval: str
    open_time_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    source: str
    fetched_at_ms: int

    @property
    def age_seconds(self) -> float:
        return max(0.0, (self.fetched_at_ms - self.open_time_ms) / 1000)


class MarketDataError(RuntimeError):
    """Raised when market data cannot be safely retrieved or validated."""


class BinanceSpotAdapter:
    """Read-only public market data adapter for Binance spot klines."""

    BASE_URL = "https://api.binance.com/api/v3/klines"
    ALLOWED_INTERVALS = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w", "1M"}

    def __init__(self, timeout_seconds: float = 8.0,
                 opener: Callable = urlopen) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.timeout_seconds = timeout_seconds
        self.opener = opener

    def fetch_latest(self, symbol: str, interval: str = "1h") -> MarketCandle:
        symbol = symbol.strip().upper()
        if not symbol or not symbol.isalnum():
            raise ValueError("symbol must contain only letters and numbers")
        if interval not in self.ALLOWED_INTERVALS:
            raise ValueError("unsupported interval")
        url = f"{self.BASE_URL}?symbol={symbol}&interval={interval}&limit=1"
        request = Request(url, headers={"User-Agent": "LiLiTrader/0.1"})
        try:
            with self.opener(request, timeout=self.timeout_seconds) as response:
                if getattr(response, "status", 200) != 200:
                    raise MarketDataError("market provider returned non-200 status")
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise MarketDataError(f"market data request failed: {type(exc).__name__}") from exc

        if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], list) or len(payload[0]) < 6:
            raise MarketDataError("unexpected market provider response")
        row = payload[0]
        try:
            candle = MarketCandle(
                symbol=symbol,
                interval=interval,
                open_time_ms=int(row[0]),
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
                source="Binance Spot API",
                fetched_at_ms=int(time.time() * 1000),
            )
        except (TypeError, ValueError, OverflowError) as exc:
            raise MarketDataError("invalid numeric market data") from exc

        numbers = (candle.open, candle.high, candle.low, candle.close, candle.volume)
        if any(not (value >= 0 and value < float("inf")) for value in numbers):
            raise MarketDataError("market data contains invalid values")
        if candle.high < max(candle.open, candle.close) or candle.low > min(candle.open, candle.close) or candle.high < candle.low:
            raise MarketDataError("OHLC consistency check failed")
        if candle.open_time_ms > candle.fetched_at_ms:
            raise MarketDataError("provider timestamp is in the future")
        return candle


class ForexDataAdapter:
    """Explicit placeholder: no Forex prices are fetched by this class."""

    def fetch_latest(self, symbol: str, interval: str = "1h") -> MarketCandle:
        raise MarketDataError(
            "Forex provider is not configured. No live Forex price is available."
        )
