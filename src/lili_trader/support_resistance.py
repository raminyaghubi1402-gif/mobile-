"""Multi-timeframe support/resistance zones from confirmed OHLC pivots.

Pure analysis only: no network access and no order execution.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from collections.abc import Sequence


@dataclass(frozen=True)
class PriceZone:
    kind: str
    lower: float
    upper: float
    midpoint: float
    touches: int
    last_touch_index: int
    strength: float


def _value(candle, name: str) -> float:
    value = candle.get(name) if isinstance(candle, dict) else getattr(candle, name)
    number = float(value)
    if not isfinite(number):
        raise ValueError("candle values must be finite")
    return number


def _atr(candles: Sequence, period: int) -> float:
    ranges = []
    for i in range(1, len(candles)):
        high = _value(candles[i], "high")
        low = _value(candles[i], "low")
        previous_close = _value(candles[i - 1], "close")
        ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    recent = ranges[-period:]
    if len(recent) < period:
        raise ValueError("not enough candles for ATR")
    value = sum(recent) / period
    if value <= 0:
        raise ValueError("ATR must be positive")
    return value


def find_support_resistance(
    candles: Sequence,
    *,
    pivot_window: int = 3,
    atr_period: int = 14,
    tolerance_atr: float = 0.5,
    min_touches: int = 2,
    max_zones: int = 4,
) -> tuple[PriceZone, ...]:
    """Find clustered, confirmed swing-high resistance and swing-low support zones.

    A pivot is used only after pivot_window candles have formed on its right.
    Zone width is based on ATR and price, and repeated pivots are clustered.
    """
    if pivot_window < 1 or atr_period < 1 or min_touches < 2 or max_zones < 1:
        raise ValueError("invalid zone parameters")
    if not isfinite(tolerance_atr) or tolerance_atr <= 0:
        raise ValueError("tolerance_atr must be positive and finite")
    if len(candles) < max(atr_period + 1, 2 * pivot_window + 3):
        raise ValueError("not enough candles for confirmed zones")

    rows = []
    previous_timestamp = None
    for candle in candles:
        high, low = _value(candle, "high"), _value(candle, "low")
        open_, close = _value(candle, "open"), _value(candle, "close")
        timestamp = candle.get("timestamp") if isinstance(candle, dict) else getattr(candle, "timestamp", None)
        if low <= 0 or high < low or high < max(open_, close) or low > min(open_, close):
            raise ValueError("invalid OHLC candle")
        if timestamp is not None:
            timestamp = int(timestamp)
            if previous_timestamp is not None and timestamp <= previous_timestamp:
                raise ValueError("candles must be strictly chronological")
            previous_timestamp = timestamp
        rows.append((high, low, close))

    atr = _atr(candles, atr_period)
    current_price = rows[-1][2]
    tolerance = max(atr * tolerance_atr, current_price * 0.002)
    pivots = {"resistance": [], "support": []}

    for index in range(pivot_window, len(rows) - pivot_window):
        high, low, _ = rows[index]
        neighbors = rows[index - pivot_window:index] + rows[index + 1:index + pivot_window + 1]
        if all(high > item[0] for item in neighbors):
            pivots["resistance"].append((high, index))
        if all(low < item[1] for item in neighbors):
            pivots["support"].append((low, index))

    zones = []
    for kind, points in pivots.items():
        clusters = []
        for price, index in sorted(points):
            matching = next((cluster for cluster in clusters
                             if abs(price - sum(p for p, _ in cluster) / len(cluster)) <= tolerance), None)
            if matching is None:
                clusters.append([(price, index)])
            else:
                matching.append((price, index))
        for cluster in clusters:
            if len(cluster) < min_touches:
                continue
            prices = [point[0] for point in cluster]
            last_touch = max(point[1] for point in cluster)
            midpoint = sum(prices) / len(prices)
            age = len(rows) - 1 - last_touch
            recency = 1.0 / (1.0 + age / max(1, len(rows) * 0.2))
            strength = len(cluster) * recency
            zones.append(PriceZone(
                kind=kind,
                lower=min(prices) - tolerance * 0.25,
                upper=max(prices) + tolerance * 0.25,
                midpoint=midpoint,
                touches=len(cluster),
                last_touch_index=last_touch,
                strength=strength,
            ))

    selected = []
    for kind in ("support", "resistance"):
        candidates = sorted(
            (zone for zone in zones if zone.kind == kind),
            key=lambda zone: (zone.strength, zone.touches, zone.last_touch_index),
            reverse=True,
        )
        selected.extend(candidates[:max_zones])
    return tuple(sorted(selected, key=lambda zone: (zone.kind, -zone.strength)))


def nearest_zone(zones: Sequence[PriceZone], price: float, kind: str) -> PriceZone | None:
    """Return the closest zone of a requested kind to a positive finite price."""
    value = float(price)
    if not isfinite(value) or value <= 0 or kind not in {"support", "resistance"}:
        raise ValueError("invalid price or zone kind")
    candidates = [zone for zone in zones if zone.kind == kind]
    return min(candidates, key=lambda zone: abs(zone.midpoint - value), default=None)
