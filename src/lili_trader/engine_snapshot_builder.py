"""Build isolated, closed-candle snapshots for independent paper strategy engines.

This module does not choose a winning strategy or execute trades. It prepares
only evidence each registered engine needs, from validated Nobitex OHLCV.
"""
from __future__ import annotations

from math import isfinite
from typing import Any, Mapping, Sequence

from .market_regime import classify_market_regime
from .nobitex_data import NobitexCandle
from .price_action_trend import classify_price_action_trend
from .support_resistance import find_support_resistance, nearest_zone


def _candle_snapshot(candle: NobitexCandle, *, now: int) -> dict[str, int | float]:
    timestamp = candle.timestamp
    fetched_at = candle.fetched_at
    if (type(timestamp) is not int or timestamp <= 0
            or type(fetched_at) is not int or fetched_at <= 0
            or timestamp + 300 > fetched_at):
        raise ValueError("5m candle close/fetch timestamps are invalid")
    values = (candle.open, candle.high, candle.low, candle.close)
    volume = candle.volume
    if isinstance(volume, bool) or not isinstance(volume, (int, float)) or not isfinite(float(volume)) or volume < 0:
        raise ValueError("5m volume must be finite and non-negative")
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
        raise ValueError("5m OHLC values must be numeric")
    open_, high, low, close = (float(value) for value in values)
    if (not all(value > 0 for value in (open_, high, low, close))
            or high < max(open_, close) or low > min(open_, close) or low > high):
        raise ValueError("5m OHLC values are inconsistent")
    return {
        "timestamp": timestamp,
        "fetched_at": fetched_at,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": float(volume),
    }


def build_independent_engine_snapshots(
    hourly: Sequence[NobitexCandle],
    fifteen: Sequence[NobitexCandle],
    five: Sequence[NobitexCandle],
    *,
    now: int,
) -> dict[str, dict[str, Any]]:
    """Prepare one separate snapshot per implemented engine from closed bars.

    The caller must first supply the latest strictly contiguous segments.
    This function rechecks the three 5m bars needed by breakout/retest logic
    and a contiguous 20-bar volume reference for the volume engine.
    Missing zones are represented as None so an engine returns WAIT rather
    than borrowing a zone of the wrong type or inventing one.
    """
    if type(now) is not int or now <= 0:
        raise ValueError("now must be a positive Unix timestamp")
    if len(five) < 3:
        raise ValueError("at least three 5m candles are required")
    recent = list(five[-3:])
    if any(
        recent[index].timestamp != recent[index - 1].timestamp + 300
        for index in range(1, len(recent))
    ):
        raise ValueError("the last three 5m candles are not contiguous")

    candle_views = [_candle_snapshot(candle, now=now) for candle in recent]
    if len(five) < 21:
        raise ValueError("at least 21 contiguous 5m candles are required for the volume engine")
    volume_rows = list(five[-21:])
    if any(
        volume_rows[index].timestamp != volume_rows[index - 1].timestamp + 300
        for index in range(1, len(volume_rows))
    ):
        raise ValueError("the 20-bar volume reference is not contiguous")
    volume_views = [_candle_snapshot(candle, now=now) for candle in volume_rows]
    ict_rows = list(five[-24:]) if len(five) >= 24 else []
    ict_contiguous = bool(ict_rows) and all(
        ict_rows[index].timestamp == ict_rows[index - 1].timestamp + 300
        for index in range(1, len(ict_rows))
    )
    ict_views = [_candle_snapshot(candle, now=now) for candle in ict_rows] if ict_contiguous else []
    if any(now - (view["timestamp"] + 300) > 900 for view in candle_views):
        raise ValueError("one of the required 5m candles is stale")

    latest_price = float(recent[-1].close)
    zones = find_support_resistance(
        fifteen, pivot_window=3, atr_period=14, tolerance_atr=0.5,
        min_touches=2, max_zones=4,
    )
    support = nearest_zone(zones, latest_price, "support")
    resistance = nearest_zone(zones, latest_price, "resistance")
    support_payload = (
        {"kind": "support", "lower": float(support.lower), "upper": float(support.upper)}
        if support is not None else None
    )
    resistance_payload = (
        {"kind": "resistance", "lower": float(resistance.lower), "upper": float(resistance.upper)}
        if resistance is not None else None
    )

    # Compute the 15m regime independently; a failure in Trend Pullback's
    # stricter 1h/5m structure must never suppress the other engines.
    trend_15m = classify_price_action_trend(list(fifteen), pivot_window=2, min_swings=2)
    market_regime = classify_market_regime(list(fifteen), trend_15m, volatility_window=20)
    try:
        trend_1h = classify_price_action_trend(list(hourly), pivot_window=2, min_swings=2)
        from .live_paper_monitor import build_trend_pullback_snapshot
        trend_snapshot = build_trend_pullback_snapshot(
            list(hourly), list(fifteen), list(five), now=now
        )
    except (ValueError, TypeError, IndexError):
        trend_1h = None
        trend_snapshot = {
            "spot_only": True,
            "regime": market_regime.trend,
            "pullback_active": False,
            "support_zone": None,
            "latest_5m": candle_views[-1],
            "structure_5m": {},
            "context": {
                "trend_1h": "unknown",
                "trend_15m": trend_15m.direction,
                "volatility_15m": market_regime.volatility,
                "regime_reason": market_regime.reason,
                "structure_reason": "شواهد اختصاصی Trend Pullback کامل نیست؛ سایر موتورها مستقل ارزیابی می‌شوند.",
                "support_zone_touches": 0,
            },
        }
    previous, latest = candle_views[-2], candle_views[-1]
    pre_breakout, breakout, retest = candle_views
    engine_regime = (
        "bullish" if market_regime.trend == "up" and market_regime.volatility in {"low", "normal"}
        else market_regime.trend
    )
    common = {"spot_only": True, "regime": engine_regime}

    return {
        "trend_pullback": trend_snapshot,
        "breakout": {
            **common,
            "resistance_zone": resistance_payload,
            "previous_5m": previous,
            "latest_5m": latest,
        },
        "breakout_retest": {
            **common,
            "resistance_zone": resistance_payload,
            "pre_breakout_5m": pre_breakout,
            "breakout_5m": breakout,
            "retest_5m": retest,
        },
        "support_resistance_rejection": {
            **common,
            "support_zone": support_payload,
            "previous_5m": previous,
            "latest_5m": latest,
        },
        "volume_trading": {
            **common,
            "volume_history_5m": volume_views[:-1],
            "latest_5m": volume_views[-1],
        },
        "ict": {
            **common,
            "now": now,
            "reference_5m": ict_views[:20] if ict_views else [],
            "liquidity_sweep_5m": ict_views[20] if ict_views else None,
            "displacement_5m": ict_views[21] if ict_views else None,
            "fvg_confirmation_5m": ict_views[22] if ict_views else None,
            "retest_5m": ict_views[23] if ict_views else None,
        },
    }
