"""Read-only public market data adapter for Nobitex (Iran).

Uses Nobitex's public OHLC history endpoint. No account token or trading API.
Official docs: https://apidocs.nobitex.ir/
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from typing import Callable


@dataclass(frozen=True)
class NobitexCandle:
    symbol: str
    resolution: str
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    source: str = "Nobitex public API"
    fetched_at: int = 0


class NobitexDataError(RuntimeError):
    """Raised when Nobitex market data is unavailable or invalid."""


class NobitexAdapter:
    BASE_URL = "https://apiv2.nobitex.ir/market/udf/history"
    ALLOWED_RESOLUTIONS = {"1", "5", "15", "30", "60", "180", "240", "360", "720", "D", "2D", "3D"}

    def __init__(self, timeout_seconds: float = 12.0, opener: Callable = urlopen):
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.timeout_seconds = timeout_seconds
        self.opener = opener

    def fetch_recent(self, symbol: str = "BTCIRT", resolution: str = "60",
                     count: int = 100) -> list[NobitexCandle]:
        symbol = symbol.strip().upper()
        if not symbol or not symbol.isalnum():
            raise ValueError("symbol must contain only letters and numbers")
        if resolution not in self.ALLOWED_RESOLUTIONS:
            raise ValueError("unsupported Nobitex resolution")
        if not isinstance(count, int) or not 1 <= count <= 500:
            raise ValueError("count must be between 1 and 500")

        now = int(time.time())
        url = (f"{self.BASE_URL}?symbol={symbol}&resolution={resolution}"
               f"&to={now}&countback={count}")
        request = Request(url, headers={"User-Agent": "LiLiTrader/0.2"})
        try:
            with self.opener(request, timeout=self.timeout_seconds) as response:
                if getattr(response, "status", 200) != 200:
                    raise NobitexDataError("Nobitex returned non-200 status")
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise NobitexDataError(f"Nobitex request failed: {type(exc).__name__}") from exc

        if not isinstance(payload, dict):
            raise NobitexDataError("unexpected Nobitex response")
        if payload.get("s") != "ok":
            raise NobitexDataError(f"Nobitex history status: {payload.get('s', 'unknown')}")

        keys = ("t", "o", "h", "l", "c")
        arrays = [payload.get(key) for key in keys]
        volume = payload.get("v", [0] * len(payload.get("t", [])))
        if any(not isinstance(arr, list) for arr in arrays) or not isinstance(volume, list):
            raise NobitexDataError("missing OHLC arrays")
        lengths = {len(arr) for arr in arrays + [volume]}
        if len(lengths) != 1 or not arrays[0]:
            raise NobitexDataError("OHLC array lengths mismatch or empty")

        fetched_at = int(time.time())
        candles = []
        for i in range(len(arrays[0])):
            try:
                candle = NobitexCandle(
                    symbol=symbol, resolution=resolution,
                    timestamp=int(arrays[0][i]),
                    open=float(arrays[1][i]), high=float(arrays[2][i]),
                    low=float(arrays[3][i]), close=float(arrays[4][i]),
                    volume=float(volume[i]), fetched_at=fetched_at,
                )
            except (TypeError, ValueError, OverflowError) as exc:
                raise NobitexDataError("invalid candle numeric value") from exc
            vals = (candle.open, candle.high, candle.low, candle.close, candle.volume)
            if any(not (v >= 0 and v < float("inf")) for v in vals):
                raise NobitexDataError("candle contains invalid values")
            if (candle.high < max(candle.open, candle.close)
                    or candle.low > min(candle.open, candle.close)
                    or candle.high < candle.low):
                raise NobitexDataError("OHLC consistency check failed")
            if candle.timestamp > fetched_at:
                raise NobitexDataError("candle timestamp is in the future")
            candles.append(candle)
        return candles
