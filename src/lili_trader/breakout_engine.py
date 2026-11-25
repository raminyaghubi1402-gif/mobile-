"""Independent close-confirmed spot breakout engine (paper signals only)."""
from __future__ import annotations

from collections.abc import Mapping
from math import isfinite
from typing import Any

from .strategy_engines import StrategyDecision, _positive_price


class BreakoutEngine:
    """Detect a fresh bullish close through a confirmed resistance zone.

    Snapshot contract: spot_only=True, regime='bullish',
    resistance_zone={kind:'resistance', lower, upper},
    previous_5m and latest_5m closed OHLC candles with timestamp/fetched_at.
    The prior close must be at/below the zone and the latest body must close
    above it. This is a breakout engine, not a pullback/retest engine.
    """

    strategy_id = "breakout"
    enabled = True

    def __init__(self, minimum_reward_risk: float = 1.5,
                 maximum_candle_age_seconds: int = 900) -> None:
        if (isinstance(minimum_reward_risk, bool)
                or not isinstance(minimum_reward_risk, (int, float))
                or not isfinite(minimum_reward_risk)
                or minimum_reward_risk < 1.5
                or type(maximum_candle_age_seconds) is not int
                or maximum_candle_age_seconds < 0):
            raise ValueError("invalid breakout risk/freshness configuration")
        self.minimum_reward_risk = float(minimum_reward_risk)
        self.maximum_candle_age_seconds = maximum_candle_age_seconds

    def evaluate(self, market_snapshot: Mapping[str, Any]) -> StrategyDecision:
        if not isinstance(market_snapshot, Mapping):
            raise ValueError("market_snapshot must be a mapping")
        if market_snapshot.get("spot_only") is not True:
            return StrategyDecision(self.strategy_id, "WAIT",
                                    "موتور شکست فقط در بازار نقدی و بدون اهرم فعال است.")
        if market_snapshot.get("regime") != "bullish":
            return StrategyDecision(self.strategy_id, "WAIT",
                                    "رژیم صعودی معتبر نیست؛ شکست خریداری نمی‌شود.")
        zone = market_snapshot.get("resistance_zone")
        previous = market_snapshot.get("previous_5m")
        latest = market_snapshot.get("latest_5m")
        if not all(isinstance(item, Mapping) for item in (zone, previous, latest)):
            return StrategyDecision(self.strategy_id, "WAIT",
                                    "ناحیه مقاومت یا دو کندل بسته‌شده در دسترس نیست.")
        try:
            if zone.get("kind") != "resistance":
                raise ValueError("resistance zone is not confirmed")
            lower = _positive_price(zone.get("lower"), "resistance lower")
            upper = _positive_price(zone.get("upper"), "resistance upper")
            if lower > upper:
                raise ValueError("resistance zone bounds are inverted")
            values = []
            for candle in (previous, latest):
                o = _positive_price(candle.get("open"), "open")
                h = _positive_price(candle.get("high"), "high")
                lo = _positive_price(candle.get("low"), "low")
                c = _positive_price(candle.get("close"), "close")
                ts, fetched = candle.get("timestamp"), candle.get("fetched_at")
                if h < max(o, c) or lo > min(o, c) or lo > h:
                    raise ValueError("inconsistent OHLC")
                if type(ts) is not int or ts <= 0 or type(fetched) is not int or fetched <= 0:
                    raise ValueError("invalid candle timestamp")
                if ts + 300 > fetched:
                    raise ValueError("candle is not closed")
                age = fetched - (ts + 300)
                if age > self.maximum_candle_age_seconds:
                    raise ValueError("candle is stale")
                values.append((o, h, lo, c, ts))
            po, ph, pl, pc, pts = values[0]
            o, h, lo, close, ts = values[1]
            if ts != pts + 300:
                raise ValueError("breakout candles are not consecutive")
            if pc > upper:
                raise ValueError("price was already above resistance; breakout is not fresh")
            if close <= upper or close <= o:
                return StrategyDecision(self.strategy_id, "WAIT",
                                        "بسته‌شدن بدنهٔ کندل بالای مقاومت تأیید نشده است.")
            if h <= upper or lo > close:
                raise ValueError("breakout candle does not validate the zone crossing")
            stop = lower
            risk = close - stop
            if risk <= 0:
                raise ValueError("no valid stop below breakout price")
            target = close + risk * self.minimum_reward_risk
            if not all(isfinite(x) for x in (stop, target)):
                raise ValueError("non-finite breakout levels")
        except ValueError as exc:
            return StrategyDecision(self.strategy_id, "WAIT",
                                    f"شکست نامعتبر است؛ ورود مسدود شد: {exc}")
        return StrategyDecision(
            self.strategy_id, "SIGNAL",
            "کندل ۵ دقیقه با بدنه بالای مقاومت بسته شده و شکست تازه است؛ فقط سیگنال Paper.",
            {
                "entry_reference": close,
                "stop_loss": stop,
                "take_profit": target,
                "minimum_reward_risk": self.minimum_reward_risk,
                "breakout_level": upper,
                "market_data_timestamp": ts,
                "market_data_fetched_at": latest["fetched_at"],
                "execution": "paper_only",
            },
        )
