"""Read-only public market data adapter for Nobitex (Iran).

Uses Nobitex's public OHLC history endpoint. No account token or trading API.
Official docs: https://apidocs.nobitex.ir/
"""
from __future__ import annotations

import json
import time
from math import isfinite
from dataclasses import dataclass
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


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
    RESOLUTION_SECONDS = {"1": 60, "5": 300, "15": 900, "30": 1800, "60": 3600, "180": 10800, "240": 14400, "360": 21600, "720": 43200, "D": 86400, "2D": 172800, "3D": 259200}
    # Nobitex UDF minute/hour candles follow Tehran clock boundaries.
    # In Unix seconds, 5m/15m bars are epoch-aligned, while 1h bars start
    # at :30 UTC (Tehran's UTC offset is half-hour based).
    CANDLE_START_OFFSETS = {"5": 0, "15": 0, "60": 1800}
    RETRYABLE_HTTP_STATUSES = {408, 425, 429, 500, 502, 503, 504}
    MAX_RETRY_DELAY_SECONDS = 2.0

    def __init__(
        self,
        timeout_seconds: float = 12.0,
        opener: Callable = urlopen,
        *,
        max_retries: int = 2,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if type(max_retries) is not int or max_retries < 0:
            raise ValueError("max_retries must be a non-negative integer")
        self.timeout_seconds = timeout_seconds
        self.opener = opener
        self.max_retries = max_retries
        self.sleep = sleep

    def fetch_history(self, symbol: str = "BTCIRT", resolution: str = "60",
                      count: int = 2000) -> list[NobitexCandle]:
        """Fetch up to 50,000 recent candles using Nobitex's 500-bar pages."""
        if not isinstance(count, int) or not 1 <= count <= 50000:
            raise ValueError("count must be between 1 and 50000")
        collected: dict[int, NobitexCandle] = {}
        page_count = (count + 499) // 500
        # Keep a single snapshot boundary across all pages so candles cannot
        # shift between requests while the market is moving.
        snapshot_to = int(time.time())
        for page in range(1, page_count + 1):
            batch = self.fetch_recent(
                symbol, resolution, 500, page=page, to_timestamp=snapshot_to
            )
            if not batch:
                break
            new_candles = [candle for candle in batch if candle.timestamp not in collected]
            if not new_candles:
                # Some upstream responses may ignore the page cursor. Stop
                # rather than silently pretending repeated rows are history.
                break
            for candle in new_candles:
                collected[candle.timestamp] = candle
        return sorted(collected.values(), key=lambda item: item.timestamp)[-count:]

    def _retry_delay(self, attempt: int, retry_after: str | None = None) -> float:
        """Return a short bounded delay; never let a server header stall a run."""
        fallback = min(0.5 * (2 ** attempt), self.MAX_RETRY_DELAY_SECONDS)
        if retry_after is None:
            return fallback
        try:
            requested = float(retry_after)
        except (TypeError, ValueError, OverflowError):
            return fallback
        if requested < 0 or requested != requested:
            return fallback
        return min(requested, self.MAX_RETRY_DELAY_SECONDS)


    def fetch_usdt_irt_rate(self, *, now: int | None = None, maximum_age_seconds: int = 120) -> float:
        """Return a fresh conservative USDT/IRT rate from Nobitex's public depth endpoint."""
        observed_at = int(time.time()) if now is None else now
        if type(observed_at) is not int or observed_at <= 0:
            raise ValueError("now must be a positive Unix timestamp")
        if type(maximum_age_seconds) is not int or maximum_age_seconds < 0:
            raise ValueError("maximum_age_seconds must be a non-negative integer")
        request = Request("https://apiv2.nobitex.ir/v2/depth/USDTIRT",
                          headers={"User-Agent": "LiLiTrader/0.2"})
        attempt = 0
        while True:
            try:
                with self.opener(request, timeout=self.timeout_seconds) as response:
                    if getattr(response, "status", 200) != 200:
                        raise NobitexDataError("Nobitex returned non-200 orderbook status")
                    payload = json.loads(response.read().decode("utf-8"))
                break
            except HTTPError as exc:
                if exc.code not in self.RETRYABLE_HTTP_STATUSES or attempt >= self.max_retries:
                    raise NobitexDataError(f"Nobitex orderbook request failed: HTTP {exc.code}") from exc
                headers = exc.headers
                retry_after = headers.get("Retry-After") if headers else None
                if exc.fp is not None:
                    exc.close()
                self.sleep(self._retry_delay(attempt, retry_after))
                attempt += 1
            except (URLError, TimeoutError, OSError) as exc:
                if attempt >= self.max_retries:
                    raise NobitexDataError(f"Nobitex orderbook request failed: {type(exc).__name__}") from exc
                self.sleep(self._retry_delay(attempt))
                attempt += 1
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise NobitexDataError(f"Nobitex orderbook JSON is invalid: {type(exc).__name__}") from exc
        if not isinstance(payload, dict) or payload.get("status") != "ok":
            raise NobitexDataError("Nobitex returned an invalid USDTIRT orderbook")
        bids, asks, raw_update = payload.get("bids"), payload.get("asks"), payload.get("lastUpdate")
        if (not isinstance(bids, list) or not bids or not isinstance(bids[0], list)
                or len(bids[0]) < 2 or not isinstance(asks, list) or not asks
                or not isinstance(asks[0], list) or len(asks[0]) < 2):
            raise NobitexDataError("USDTIRT orderbook has no valid best bid/ask")
        try:
            raw_numbers = (raw_update, bids[0][0], bids[0][1], asks[0][0], asks[0][1])
            if any(isinstance(value, bool) for value in raw_numbers):
                raise ValueError("boolean orderbook value")
            update_number = float(raw_update)
            if not isfinite(update_number) or not update_number.is_integer():
                raise ValueError("invalid orderbook update timestamp")
            update_ms = int(update_number)
            best_bid, best_ask = float(bids[0][0]), float(asks[0][0])
            bid_volume, ask_volume = float(bids[0][1]), float(asks[0][1])
        except (TypeError, ValueError, OverflowError) as exc:
            raise NobitexDataError("USDTIRT orderbook contains invalid numeric values") from exc
        if (update_ms <= 1_000_000_000_000
                or not all(value > 0 and value < float("inf")
                           for value in (best_bid, best_ask, bid_volume, ask_volume))
                or best_ask < best_bid):
            raise NobitexDataError("USDTIRT orderbook values are invalid or crossed")
        age = observed_at - update_ms // 1000
        if age < -5 or age > maximum_age_seconds:
            raise NobitexDataError("USDTIRT orderbook is future-dated or stale")
        return best_bid

    def fetch_recent(self, symbol: str = "BTCIRT", resolution: str = "60",
                     count: int = 100, *, page: int = 1,
                     to_timestamp: int | None = None,
                     from_timestamp: int | None = None) -> list[NobitexCandle]:
        symbol = symbol.strip().upper()
        if not symbol or not symbol.isalnum():
            raise ValueError("symbol must contain only letters and numbers")
        if resolution not in self.ALLOWED_RESOLUTIONS:
            raise ValueError("unsupported Nobitex resolution")
        if not isinstance(count, int) or not 1 <= count <= 500:
            raise ValueError("count must be between 1 and 500")
        if not isinstance(page, int) or page < 1:
            raise ValueError("page must be a positive integer")

        if to_timestamp is None:
            to_timestamp = int(time.time())
        if not isinstance(to_timestamp, int) or to_timestamp <= 0:
            raise ValueError("to_timestamp must be a positive Unix timestamp")
        if from_timestamp is None:
            url = (f"{self.BASE_URL}?symbol={symbol}&resolution={resolution}"
                   f"&to={to_timestamp}&countback={count}&page={page}")
        else:
            if type(from_timestamp) is not int or from_timestamp <= 0:
                raise ValueError("from_timestamp must be a positive integer")
            if from_timestamp > to_timestamp:
                raise ValueError("from_timestamp must not exceed to_timestamp")
            interval = self.RESOLUTION_SECONDS[resolution]
            requested_bars = (to_timestamp - from_timestamp) // interval + 1
            if requested_bars > 500:
                raise ValueError("time range must contain at most 500 candles")
            # Explicit from/to avoids relying on countback paging for targeted gaps.
            url = (f"{self.BASE_URL}?symbol={symbol}&resolution={resolution}"
                   f"&from={from_timestamp}&to={to_timestamp}&page={page}")
        request = Request(url, headers={"User-Agent": "LiLiTrader/0.2"})

        attempt = 0
        while True:
            try:
                with self.opener(request, timeout=self.timeout_seconds) as response:
                    if getattr(response, "status", 200) != 200:
                        raise NobitexDataError("Nobitex returned non-200 status")
                    payload = json.loads(response.read().decode("utf-8"))
                break
            except HTTPError as exc:
                if exc.code not in self.RETRYABLE_HTTP_STATUSES or attempt >= self.max_retries:
                    raise NobitexDataError(
                        f"Nobitex request failed: {type(exc).__name__}"
                    ) from exc
                headers = exc.headers
                retry_after = headers.get("Retry-After") if headers else None
                if exc.fp is not None:
                    exc.close()
                self.sleep(self._retry_delay(attempt, retry_after))
                attempt += 1
            except (URLError, TimeoutError, OSError) as exc:
                if attempt >= self.max_retries:
                    raise NobitexDataError(
                        f"Nobitex request failed: {type(exc).__name__}"
                    ) from exc
                self.sleep(self._retry_delay(attempt))
                attempt += 1
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise NobitexDataError(
                    f"Nobitex request failed: {type(exc).__name__}"
                ) from exc

        if not isinstance(payload, dict):
            raise NobitexDataError("unexpected Nobitex response")
        if payload.get("s") == "no_data":
            return []
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
                raw_timestamp = arrays[0][i]
                # Timestamps are Unix seconds, not prices: reject booleans,
                # fractional values, and non-positive values rather than
                # silently coercing them into a different candle.
                if isinstance(raw_timestamp, bool):
                    raise ValueError("boolean timestamp")
                timestamp_value = float(raw_timestamp)
                if (not timestamp_value.is_integer()
                        or timestamp_value <= 0
                        or timestamp_value >= float("inf")):
                    raise ValueError("invalid timestamp")
                timestamp = int(timestamp_value)
                expected_offset = self.CANDLE_START_OFFSETS.get(resolution)
                if (expected_offset is not None
                        and timestamp % self.RESOLUTION_SECONDS[resolution] != expected_offset):
                    raise NobitexDataError(
                        "candle timestamp is not aligned to the Nobitex session boundary"
                    )
                raw_values = (
                    arrays[1][i], arrays[2][i], arrays[3][i],
                    arrays[4][i], volume[i],
                )
                if any(isinstance(value, bool) for value in raw_values):
                    raise ValueError("boolean OHLCV value")
                candle = NobitexCandle(
                    symbol=symbol, resolution=resolution,
                    timestamp=int(timestamp_value),
                    open=float(arrays[1][i]), high=float(arrays[2][i]),
                    low=float(arrays[3][i]), close=float(arrays[4][i]),
                    volume=float(volume[i]), fetched_at=fetched_at,
                )
            except (TypeError, ValueError, OverflowError) as exc:
                raise NobitexDataError("invalid candle numeric value") from exc
            vals = (candle.open, candle.high, candle.low, candle.close, candle.volume)
            if any(not (v >= 0 and v < float("inf")) for v in vals):
                raise NobitexDataError("candle contains invalid values")
            if any(v <= 0 for v in (candle.open, candle.high, candle.low, candle.close)):
                raise NobitexDataError("OHLC prices must be positive")
            if (candle.high < max(candle.open, candle.close)
                    or candle.low > min(candle.open, candle.close)
                    or candle.high < candle.low):
                raise NobitexDataError("OHLC consistency check failed")
            if candle.timestamp > fetched_at:
                raise NobitexDataError("candle timestamp is in the future")
            candles.append(candle)
        return candles
