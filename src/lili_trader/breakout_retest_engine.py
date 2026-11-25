"""Independent bullish breakout-retest engine for spot paper signals only."""
from __future__ import annotations

from collections.abc import Mapping
from math import isfinite
from typing import Any

from .strategy_engines import StrategyDecision, _positive_price


class BreakoutRetestEngine:
    """Buy only after a fresh close-confirmed breakout and a confirmed retest.

    Snapshot contract:
      spot_only=True, regime='bullish'
      resistance_zone={kind:'resistance', lower, upper}
      pre_breakout_5m, breakout_5m, retest_5m: closed OHLC mappings with
      timestamp and fetched_at. All three candles must be consecutive 5m bars.
    The retest candle must trade into (not through) the old resistance zone,
    close back above its upper edge, and close bullish. Signals are paper-only.
    """

    strategy_id = "breakout_retest"
    enabled = True

    def __init__(self, minimum_reward_risk: float = 1.5,
                 maximum_candle_age_seconds: int = 900) -> None:
        if (isinstance(minimum_reward_risk, bool)
                or not isinstance(minimum_reward_risk, (int, float))
                or not isfinite(minimum_reward_risk)
                or minimum_reward_risk < 1.5
                or type(maximum_candle_age_seconds) is not int
                or maximum_candle_age_seconds < 0):
            raise ValueError("invalid breakout-retest risk/freshness configuration")
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
                                    "رژیم صعودی معتبر نیست؛ خرید شکست‌مجدد مسدود است.")
        zone = market_snapshot.get("resistance_zone")
        candles = tuple(market_snapshot.get(key) for key in (
            "pre_breakout_5m", "breakout_5m", "retest_5m"
        ))
        if not isinstance(zone, Mapping) or not all(isinstance(c, Mapping) for c in candles):
            return StrategyDecision(self.strategy_id, "WAIT",
                                    "ناحیه مقاومت یا سه کندل لازم برای شکست و بازآزمایی موجود نیست.")
        try:
            if zone.get("kind") != "resistance":
                raise ValueError("resistance zone is not confirmed")
            lower = _positive_price(zone.get("lower"), "resistance lower")
            upper = _positive_price(zone.get("upper"), "resistance upper")
            if lower > upper:
                raise ValueError("resistance zone bounds are inverted")

            parsed = []
            for candle in candles:
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

            prior, breakout, retest = parsed
            if not (breakout[4] == prior[4] + 300
                    and retest[4] == breakout[4] + 300):
                raise ValueError("breakout/retest candles are not consecutive")
            if prior[3] > upper:
                raise ValueError("price was already above resistance; breakout is not fresh")
            if breakout[3] <= upper or breakout[3] <= breakout[0]:
                return StrategyDecision(
                    self.strategy_id, "WAIT",
                    "شکست اولیه با بسته‌شدن صعودی بدنه بالای مقاومت تأیید نشده است."
                )
            if retest[2] > upper:
                return StrategyDecision(
                    self.strategy_id, "WAIT",
                    "قیمت هنوز به ناحیه مقاومت شکسته‌شده برای بازآزمایی نرسیده است."
                )
            if retest[2] < lower:
                raise ValueError("retest traded through the full resistance zone")
            if retest[3] <= upper or retest[3] <= retest[0]:
                return StrategyDecision(
                    self.strategy_id, "WAIT",
                    "بازآزمایی با بسته‌شدن صعودی دوباره بالای مقاومت تأیید نشده است."
                )

            entry = retest[3]
            stop = lower
            risk = entry - stop
            if risk <= 0:
                raise ValueError("no valid structural stop below retest entry")
            target = entry + risk * self.minimum_reward_risk
            if not all(isfinite(value) for value in (entry, stop, target)):
                raise ValueError("non-finite breakout-retest levels")
        except ValueError as exc:
            return StrategyDecision(
                self.strategy_id, "WAIT",
                f"شکست‌مجدد معتبر نیست؛ ورود مسدود شد: {exc}"
            )

        return StrategyDecision(
            self.strategy_id, "SIGNAL",
            "شکست اولیه با بدنه تأیید شد؛ قیمت مقاومت را بدون شکستن کف ناحیه بازآزمایی کرد و بالای آن صعودی بسته شد؛ فقط سیگنال Paper.",
            {
                "entry_reference": entry,
                "stop_loss": stop,
                "take_profit": target,
                "minimum_reward_risk": self.minimum_reward_risk,
                "breakout_level": upper,
                "breakout_timestamp": breakout[4],
                "retest_timestamp": retest[4],
                "market_data_timestamp": retest[4],
                "market_data_fetched_at": retest[5],
                "execution": "paper_only",
            },
        )
