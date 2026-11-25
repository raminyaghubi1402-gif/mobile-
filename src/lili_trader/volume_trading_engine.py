"""Independent relative-volume breakout engine for spot paper signals only."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import isfinite
from statistics import median
from typing import Any

from .strategy_engines import StrategyDecision, _positive_price, _target_for_minimum_reward_risk


class VolumeTradingEngine:
    """Trade a bullish close breakout accompanied by exceptional relative volume.

    Snapshot contract: spot_only=True, regime='bullish', exactly 20 prior
    contiguous closed 5m candles in volume_history_5m, and latest_5m.
    Current volume must exceed every reference volume and the candle must
    close above every reference high with a bullish body. No fixed volume
    multiplier or legacy multi-timeframe confirmation is used.
    """

    strategy_id = "volume_trading"
    enabled = True

    def __init__(self, minimum_reward_risk: float = 1.5,
                 maximum_candle_age_seconds: int = 900) -> None:
        if (isinstance(minimum_reward_risk, bool)
                or not isinstance(minimum_reward_risk, (int, float))
                or not isfinite(minimum_reward_risk)
                or minimum_reward_risk < 1.5
                or type(maximum_candle_age_seconds) is not int
                or maximum_candle_age_seconds < 0):
            raise ValueError("invalid volume-engine risk/freshness configuration")
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
                                    "رژیم صعودی معتبر نیست؛ ورود حجمی مسدود است.")

        history = market_snapshot.get("volume_history_5m")
        latest = market_snapshot.get("latest_5m")
        if (not isinstance(history, Sequence) or isinstance(history, (str, bytes))
                or len(history) != 20 or not isinstance(latest, Mapping)):
            return StrategyDecision(self.strategy_id, "WAIT",
                                    "۲۰ کندل مرجع یا کندل نهایی برای سنجش حجم موجود نیست.")
        if any(not isinstance(item, Mapping) for item in history):
            return StrategyDecision(self.strategy_id, "WAIT",
                                    "ساختار دادهٔ حجم نامعتبر است.")

        try:
            rows = []
            for candle in (*history, latest):
                open_ = _positive_price(candle.get("open"), "open")
                high = _positive_price(candle.get("high"), "high")
                low = _positive_price(candle.get("low"), "low")
                close = _positive_price(candle.get("close"), "close")
                volume = candle.get("volume")
                timestamp = candle.get("timestamp")
                fetched_at = candle.get("fetched_at")
                if (isinstance(volume, bool) or not isinstance(volume, (int, float))
                        or not isfinite(volume) or volume < 0):
                    raise ValueError("volume must be finite and non-negative")
                if high < max(open_, close) or low > min(open_, close) or low > high:
                    raise ValueError("inconsistent OHLC")
                if (type(timestamp) is not int or timestamp <= 0
                        or type(fetched_at) is not int or fetched_at < timestamp + 300):
                    raise ValueError("invalid or unclosed 5m candle")
                rows.append((open_, high, low, close, float(volume), timestamp, fetched_at))

            for prior, current in zip(rows, rows[1:]):
                if current[5] != prior[5] + 300:
                    raise ValueError("volume reference candles are not contiguous")

            current = rows[-1]
            if current[6] - (current[5] + 300) > self.maximum_candle_age_seconds:
                raise ValueError("latest volume candle is stale")
            reference = rows[:-1]
            reference_high = max(row[1] for row in reference)
            reference_volume = max(row[4] for row in reference)
            if current[4] <= reference_volume:
                return StrategyDecision(
                    self.strategy_id, "WAIT",
                    "حجم کندل جاری از بیشترین حجم ۲۰ کندل مرجع بیشتر نیست."
                )
            if current[3] <= reference_high or current[3] <= current[0]:
                return StrategyDecision(
                    self.strategy_id, "WAIT",
                    "شکست صعودی با بسته‌شدن بدنه بالای سقف ۲۰ کندل مرجع تأیید نشده است."
                )

            entry = current[3]
            stop = current[2]
            risk = entry - stop
            if risk <= 0:
                raise ValueError("no valid structural stop below entry")
            target = _target_for_minimum_reward_risk(entry, stop, self.minimum_reward_risk)
            if not all(isfinite(value) for value in (entry, stop, target)):
                raise ValueError("non-finite volume breakout levels")
            typical_volume = median(row[4] for row in reference)
            relative_volume = current[4] / typical_volume if typical_volume > 0 else None
        except ValueError as exc:
            return StrategyDecision(
                self.strategy_id, "WAIT",
                f"شرط حجمی معتبر نیست؛ ورود مسدود شد: {exc}"
            )

        return StrategyDecision(
            self.strategy_id,
            "SIGNAL",
            "حجم کندل جاری از تمام ۲۰ کندل مرجع بیشتر است و بدنه با قدرت بالای سقف آن‌ها بسته شده؛ فقط سیگنال Paper.",
            {
                "entry_reference": entry,
                "stop_loss": stop,
                "take_profit": target,
                "minimum_reward_risk": self.minimum_reward_risk,
                "reference_high": reference_high,
                "current_volume": current[4],
                "maximum_reference_volume": reference_volume,
                "median_reference_volume": typical_volume,
                "relative_volume_vs_median": relative_volume,
                "market_data_timestamp": current[5],
                "market_data_fetched_at": current[6],
                "execution": "paper_only",
            },
        )
