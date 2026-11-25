"""Independent bullish support-rejection engine for spot paper signals only."""
from __future__ import annotations

from collections.abc import Mapping
from math import isfinite
from typing import Any

from .strategy_engines import StrategyDecision, _positive_price, _target_for_minimum_reward_risk


class SupportResistanceRejectionEngine:
    """Buy a confirmed bullish rejection of a validated support zone.

    Snapshot contract: spot_only=True, regime='bullish', support_zone with
    kind/lower/upper, and two consecutive closed 5m candles. The prior close
    must be above support; the latest candle must test but not trade through
    the zone, then close bullish back above its upper edge.
    """

    strategy_id = "support_resistance_rejection"
    enabled = True

    def __init__(self, minimum_reward_risk: float = 1.5,
                 maximum_candle_age_seconds: int = 900) -> None:
        if (isinstance(minimum_reward_risk, bool)
                or not isinstance(minimum_reward_risk, (int, float))
                or not isfinite(minimum_reward_risk)
                or minimum_reward_risk < 1.5
                or type(maximum_candle_age_seconds) is not int
                or maximum_candle_age_seconds < 0):
            raise ValueError("invalid support-rejection risk/freshness configuration")
        self.minimum_reward_risk = float(minimum_reward_risk)
        self.maximum_candle_age_seconds = maximum_candle_age_seconds

    def evaluate(self, market_snapshot: Mapping[str, Any]) -> StrategyDecision:
        if not isinstance(market_snapshot, Mapping):
            raise ValueError("market_snapshot must be a mapping")
        if market_snapshot.get("spot_only") is not True:
            return StrategyDecision(self.strategy_id, "WAIT",
                                    "این موتور فقط برای بازار نقدی و بدون اهرم فعال است.")
        if market_snapshot.get("regime") != "bullish":
            return StrategyDecision(self.strategy_id, "WAIT",
                                    "رژیم صعودی معتبر نیست؛ خرید از حمایت مسدود است.")
        zone = market_snapshot.get("support_zone")
        previous = market_snapshot.get("previous_5m")
        latest = market_snapshot.get("latest_5m")
        if not all(isinstance(item, Mapping) for item in (zone, previous, latest)):
            return StrategyDecision(self.strategy_id, "WAIT",
                                    "ناحیه حمایت یا دو کندل لازم برای رد قیمت موجود نیست.")
        try:
            if zone.get("kind") != "support":
                raise ValueError("support zone is not confirmed")
            lower = _positive_price(zone.get("lower"), "support lower")
            upper = _positive_price(zone.get("upper"), "support upper")
            if lower > upper:
                raise ValueError("support zone bounds are inverted")
            parsed = []
            for candle in (previous, latest):
                o = _positive_price(candle.get("open"), "open")
                h = _positive_price(candle.get("high"), "high")
                lo = _positive_price(candle.get("low"), "low")
                close = _positive_price(candle.get("close"), "close")
                ts, fetched = candle.get("timestamp"), candle.get("fetched_at")
                if h < max(o, close) or lo > min(o, close) or lo > h:
                    raise ValueError("inconsistent OHLC")
                if type(ts) is not int or ts <= 0 or type(fetched) is not int:
                    raise ValueError("invalid candle timestamp or fetch time")
                if fetched < ts + 300:
                    raise ValueError("a candle is not closed")
                if fetched - (ts + 300) > self.maximum_candle_age_seconds:
                    raise ValueError("a candle is stale")
                parsed.append((o, h, lo, close, ts, fetched))
            prior, rejection = parsed
            if rejection[4] != prior[4] + 300:
                raise ValueError("support-rejection candles are not consecutive")
            if prior[3] <= upper:
                raise ValueError("price was already inside/below support; rejection is not fresh")
            if rejection[2] > upper:
                return StrategyDecision(self.strategy_id, "WAIT",
                                        "قیمت هنوز ناحیه حمایت را آزمایش نکرده است.")
            if rejection[2] < lower:
                raise ValueError("rejection candle traded through the full support zone")
            if rejection[3] <= upper or rejection[3] <= rejection[0]:
                return StrategyDecision(self.strategy_id, "WAIT",
                                        "رد صعودی حمایت با بسته‌شدن بدنه بالای ناحیه تأیید نشده است.")
            entry = rejection[3]
            stop = lower
            risk = entry - stop
            if risk <= 0:
                raise ValueError("no valid structural stop below entry")
            target = _target_for_minimum_reward_risk(entry, stop, self.minimum_reward_risk)
            if not all(isfinite(value) for value in (entry, stop, target)):
                raise ValueError("non-finite support-rejection levels")
        except ValueError as exc:
            return StrategyDecision(
                self.strategy_id, "WAIT",
                f"رد حمایت معتبر نیست؛ ورود مسدود شد: {exc}"
            )
        return StrategyDecision(
            self.strategy_id, "SIGNAL",
            "قیمت حمایت معتبر را آزمایش کرد، کف ناحیه را نشکست و با کندل صعودی بالای حمایت بسته شد؛ فقط سیگنال Paper.",
            {
                "entry_reference": entry,
                "stop_loss": stop,
                "take_profit": target,
                "minimum_reward_risk": self.minimum_reward_risk,
                "support_zone_lower": lower,
                "support_zone_upper": upper,
                "market_data_timestamp": rejection[4],
                "market_data_fetched_at": rejection[5],
                "execution": "paper_only",
            },
        )
