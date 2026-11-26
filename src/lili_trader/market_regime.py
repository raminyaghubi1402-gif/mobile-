"""Rule-based explainable market regime and strategy selector (research only)."""
from __future__ import annotations
from dataclasses import dataclass, replace
from math import isfinite
from collections.abc import Mapping, Sequence
from .strategy_engines import STRATEGY_CATALOG

@dataclass(frozen=True)
class MarketRegime:
    trend: str
    volatility: str
    structure_quality: str
    range_position: str
    allowed_strategies: tuple[str, ...]
    confidence: str
    reason: str

@dataclass(frozen=True)
class StrategyCandidate:
    name: str
    eligible: bool
    reason: str

def _get(candle, key):
    raw = candle.get(key) if isinstance(candle, Mapping) else getattr(candle, key, None)
    if isinstance(raw, bool):
        raise ValueError("OHLC values must be numeric, not boolean")
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("OHLC values must be numeric") from None
    if not isfinite(value) or value <= 0:
        raise ValueError("OHLC values must be finite and positive")
    return value

def classify_market_regime(candles: Sequence, trend_state, *, volatility_window: int = 20) -> MarketRegime:
    """Classify trend, volatility and price location from closed OHLC candles only."""
    if type(volatility_window) is not int or volatility_window < 10:
        raise ValueError("volatility_window must be an integer >= 10")
    if (not isinstance(candles, Sequence) or isinstance(candles, (str, bytes))
            or len(candles) < volatility_window):
        return MarketRegime("unknown", "unknown", "insufficient", "unknown", (), "low",
                           "برای تشخیص رژیم، کندل بسته‌شده کافی نیست.")
    rows = []
    for c in candles[-volatility_window:]:
        high, low, close = (_get(c, k) for k in ("high", "low", "close"))
        open_ = _get(c, "open")
        if high < max(open_, close) or low > min(open_, close):
            raise ValueError("invalid OHLC")
        rows.append((high, low, close))
    ranges = [(h-l)/cl for h,l,cl in rows if cl > 0]
    if len(ranges) < volatility_window:
        return MarketRegime("unknown", "unknown", "insufficient", "unknown", (), "low",
                           "داده نوسان معتبر نیست.")
    ordered = sorted(ranges)
    median = ordered[len(ordered)//2]
    # Use the median of the latest five bars rather than their mean:
    # one isolated wick/spike should not label the whole regime "high volatility".
    recent_values = sorted(ranges[-5:])
    recent = recent_values[len(recent_values)//2]
    if median <= 0:
        vol = "unknown"
    elif recent > median * 2.2:
        vol = "high"
    elif recent < median * 0.65:
        vol = "low"
    else:
        vol = "normal"

    direction = getattr(trend_state, "direction", "unknown")
    highs = getattr(trend_state, "last_two_swing_highs", None)
    lows = getattr(trend_state, "last_two_swing_lows", None)
    aligned = bool(highs and lows and len(highs)==2 and len(lows)==2)
    if direction in ("up", "down") and aligned:
        trend, quality = direction, "aligned"
    elif direction == "range":
        trend, quality = "range", "mixed"
    else:
        trend, quality = ("transition", "mixed") if aligned else ("unknown", "insufficient")

    recent_low, recent_high = min(r[1] for r in rows), max(r[0] for r in rows)
    last_close = rows[-1][2]
    width = recent_high - recent_low
    if width <= 0:
        pos = "unknown"
    else:
        ratio = (last_close - recent_low) / width
        pos = "lower" if ratio <= 0.33 else ("upper" if ratio >= 0.67 else "middle")

    strategies = []
    if trend in ("up", "down") and vol != "high":
        # These are independent engine IDs, not a combined confirmation chain.
        # Direction and venue constraints are supplied in the engine snapshot.
        strategies.extend(name for name in STRATEGY_CATALOG if name != "range_trading")
    elif trend == "range" and vol != "high":
        strategies.append("no_trade_range_policy")
    if trend in ("unknown", "transition") or vol == "high":
        strategies = ["no_trade_wait"]
    confidence = "moderate" if quality == "aligned" and vol in ("low", "normal") else "low"
    reason = f"ساختار={trend}، نوسان={vol}، کیفیت={quality}، جایگاه قیمت={pos}؛ تشخیص قطعی یا پیش‌بینی نیست."
    return MarketRegime(trend, vol, quality, pos, tuple(strategies), confidence, reason)

def apply_primary_trend(regime: MarketRegime, trend_1h: str, trend_15m: str) -> MarketRegime:
    """Use 1h direction unless it is explicitly range; then fall back to 15m.

    Volatility and price location remain measured on 15m. Unknown or transition
    1h states do not silently fall back to a lower timeframe.
    """
    valid = {"up", "down", "range", "transition", "unknown"}
    if trend_1h not in valid or trend_15m not in valid:
        raise ValueError("timeframe trend must be a known price-action state")
    primary_trend = trend_15m if trend_1h == "range" else trend_1h
    primary_timeframe = "15m" if trend_1h == "range" else "1h"
    if primary_trend in ("up", "down") and regime.volatility not in ("high", "unknown"):
        allowed = tuple(name for name in STRATEGY_CATALOG if name != "range_trading")
    elif primary_trend == "range" and regime.volatility not in ("high", "unknown"):
        allowed = ("no_trade_range_policy",)
    else:
        allowed = ("no_trade_wait",)
    return replace(
        regime,
        trend=primary_trend,
        allowed_strategies=allowed,
        reason=(f"جهت اصلی از {primary_timeframe}: {primary_trend}؛ "
                f"روند ۱ساعته={trend_1h}، روند ۱۵دقیقه={trend_15m}. "
                f"{regime.reason}"),
    )


def select_strategy_candidates(
    regime: MarketRegime, *, allow_short: bool = False
) -> tuple[StrategyCandidate, ...]:
    """Select independent engines by regime; spot-only downtrends fail closed."""
    if type(allow_short) is not bool:
        raise ValueError("allow_short must be boolean")
    if regime.trend in ("unknown", "transition") or regime.volatility in ("high", "unknown"):
        return (StrategyCandidate("no_trade_wait", True,
                                 "رژیم نامشخص یا نوسان شدید؛ صبر و حفظ سرمایه."),)
    if regime.trend == "range":
        return (StrategyCandidate("no_trade_range_policy", True,
                                 "طبق سیاست فعلی پروژه، در رنج معامله نمی‌کنیم."),)
    if regime.trend == "down" and not allow_short:
        return (StrategyCandidate("no_trade_wait", True,
                                 "بازار نزولی است و حساب نقدی اجازهٔ فروش استقراضی نمی‌دهد."),)
    candidates = [
        StrategyCandidate(name, True,
            "موتور مستقل برای ارزیابی انتخاب شده است؛ نتیجهٔ آن به تأیید موتورهای دیگر وابسته نیست.")
        for name in regime.allowed_strategies if name in STRATEGY_CATALOG
    ]
    if not candidates:
        candidates.append(StrategyCandidate("no_trade_wait", True,
                                           "هیچ استراتژی مناسب این رژیم نیست."))
    return tuple(candidates)
