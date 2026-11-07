"""Rule-based explainable market regime and strategy selector (research only)."""
from __future__ import annotations
from dataclasses import dataclass
from math import isfinite
from collections.abc import Sequence

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
    raw = candle.get(key) if isinstance(candle, dict) else getattr(candle, key)
    value = float(raw)
    if not isfinite(value):
        raise ValueError("OHLC values must be finite")
    return value

def classify_market_regime(candles: Sequence, trend_state, *, volatility_window: int = 20) -> MarketRegime:
    """Classify trend, volatility and price location from closed OHLC candles only."""
    if volatility_window < 10 or not isinstance(candles, Sequence) or len(candles) < volatility_window:
        return MarketRegime("unknown", "unknown", "insufficient", "unknown", (), "low",
                           "برای تشخیص رژیم، کندل بسته‌شده کافی نیست.")
    rows = []
    for c in candles[-volatility_window:]:
        high, low, close = (_get(c, k) for k in ("high", "low", "close"))
        open_ = _get(c, "open")
        if low <= 0 or high < max(open_, close) or low > min(open_, close):
            raise ValueError("invalid OHLC")
        rows.append((high, low, close))
    ranges = [(h-l)/cl for h,l,cl in rows if cl > 0]
    if len(ranges) < volatility_window:
        return MarketRegime("unknown", "unknown", "insufficient", "unknown", (), "low",
                           "داده نوسان معتبر نیست.")
    ordered = sorted(ranges)
    median = ordered[len(ordered)//2]
    recent = sum(ranges[-5:]) / min(5, len(ranges))
    if median <= 0:
        vol = "unknown"
    elif recent > median * 1.8:
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
    if trend == "up" and vol != "high":
        strategies.extend(("trend_pullback_buy", "support_rejection_buy", "breakout_retest_buy"))
    elif trend == "down" and vol != "high":
        strategies.extend(("trend_pullback_sell", "resistance_rejection_sell", "breakdown_retest_sell"))
    elif trend == "range" and vol != "high":
        strategies.append("no_trade_range_policy")
    if trend in ("unknown", "transition") or vol == "high":
        strategies = ["no_trade_wait"]
    confidence = "moderate" if quality == "aligned" and vol in ("low", "normal") else "low"
    reason = f"ساختار={trend}، نوسان={vol}، کیفیت={quality}، جایگاه قیمت={pos}؛ تشخیص قطعی یا پیش‌بینی نیست."
    return MarketRegime(trend, vol, quality, pos, tuple(strategies), confidence, reason)

def select_strategy_candidates(regime: MarketRegime) -> tuple[StrategyCandidate, ...]:
    """Return eligible research candidates; final setup validation remains mandatory."""
    known = {
        "trend_pullback_buy", "support_rejection_buy", "breakout_retest_buy",
        "trend_pullback_sell", "resistance_rejection_sell", "breakdown_retest_sell",
    }
    if regime.trend in ("unknown", "transition") or regime.volatility in ("high", "unknown"):
        return (StrategyCandidate("no_trade_wait", True,
                                 "رژیم نامشخص یا نوسان شدید؛ صبر و حفظ سرمایه."),)
    if regime.trend == "range":
        return (StrategyCandidate("no_trade_range_policy", True,
                                 "طبق سیاست فعلی پروژه، در رنج معامله نمی‌کنیم."),)
    candidates = []
    for name in regime.allowed_strategies:
        if name in known:
            candidates.append(StrategyCandidate(name, True,
                "فقط کاندید بررسی است؛ تأیید ستاپ، ناحیه، فضای سود و حد ابطال لازم است."))
    if not candidates:
        candidates.append(StrategyCandidate("no_trade_wait", True,
                                           "هیچ استراتژی مناسب این رژیم نیست."))
    return tuple(candidates)
