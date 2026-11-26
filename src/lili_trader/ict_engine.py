"""ICT-style sell-side sweep, bullish displacement, FVG and retest engine.

This is a deliberately narrow, explainable ICT-inspired setup for spot-long
paper research. It is not a claim to implement every ICT concept.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import isfinite

from .strategy_engines import DEFAULT_ENTRY_SLIPPAGE_RATE, StrategyDecision, _positive_price, _target_for_minimum_reward_risk


class ICTEngine:
    strategy_id = "ict"
    enabled = True

    def __init__(self, minimum_reward_risk: float = 1.5,
                 maximum_candle_age_seconds: int = 900) -> None:
        if (isinstance(minimum_reward_risk, bool)
                or not isinstance(minimum_reward_risk, (int, float))
                or not isfinite(minimum_reward_risk)
                or minimum_reward_risk < 1.5
                or type(maximum_candle_age_seconds) is not int
                or maximum_candle_age_seconds < 0):
            raise ValueError("invalid ICT risk/freshness configuration")
        self.minimum_reward_risk = float(minimum_reward_risk)
        self.maximum_candle_age_seconds = maximum_candle_age_seconds

    def evaluate(self, market_snapshot: Mapping[str, object]) -> StrategyDecision:
        if not isinstance(market_snapshot, Mapping):
            raise ValueError("market_snapshot must be a mapping")
        if market_snapshot.get("spot_only") is not True:
            return StrategyDecision(self.strategy_id, "WAIT",
                                    "موتور ICT فقط برای خرید نقدی و بدون اهرم فعال است.")
        if market_snapshot.get("regime") != "bullish":
            return StrategyDecision(self.strategy_id, "WAIT",
                                    "رژیم صعودی معتبر نیست؛ ستاپ ICT مسدود است.")

        reference = market_snapshot.get("reference_5m")
        names = ("liquidity_sweep_5m", "displacement_5m",
                 "fvg_confirmation_5m", "retest_5m")
        if (not isinstance(reference, Sequence) or isinstance(reference, (str, bytes))
                or len(reference) != 20
                or any(not isinstance(row, Mapping) for row in reference)):
            return StrategyDecision(self.strategy_id, "WAIT",
                                    "۲۰ کندل مرجع نقدینگی و ساختار موجود نیست.")
        pattern = [market_snapshot.get(name) for name in names]
        if any(not isinstance(row, Mapping) for row in pattern):
            return StrategyDecision(self.strategy_id, "WAIT",
                                    "چهار کندل متوالیِ sweep، displacement، FVG و retest ناقص است.")

        try:
            rows = []
            for candle in (*reference, *pattern):
                open_ = _positive_price(candle.get("open"), "open")
                high = _positive_price(candle.get("high"), "high")
                low = _positive_price(candle.get("low"), "low")
                close = _positive_price(candle.get("close"), "close")
                timestamp, fetched_at = candle.get("timestamp"), candle.get("fetched_at")
                if high < max(open_, close) or low > min(open_, close) or low > high:
                    raise ValueError("inconsistent OHLC")
                if (type(timestamp) is not int or timestamp <= 0
                        or type(fetched_at) is not int or fetched_at < timestamp + 300):
                    raise ValueError("invalid or unclosed candle")
                rows.append((open_, high, low, close, timestamp, fetched_at))
            for previous, current in zip(rows, rows[1:]):
                if current[4] != previous[4] + 300:
                    raise ValueError("ICT reference/pattern candles are not contiguous")

            now = market_snapshot.get("now")
            if type(now) is not int or now <= 0:
                raise ValueError("snapshot observation time is missing")
            latest = rows[-1]
            latest_age = now - (latest[4] + 300)
            if latest_age < 0 or latest_age > self.maximum_candle_age_seconds:
                raise ValueError("latest retest candle is future-dated or stale")

            ref = rows[:20]
            sweep, displacement, fvg, retest = rows[20:]
            sell_side_liquidity = min(row[2] for row in ref)
            internal_high = max(row[1] for row in ref[-5:])

            if not (sweep[2] < sell_side_liquidity and sweep[3] > sell_side_liquidity):
                return StrategyDecision(
                    self.strategy_id, "WAIT",
                    "قیمت نقدینگی زیر کف‌های مرجع را جمع نکرده و بالای آن برنگشته است."
                )
            if not (displacement[3] > displacement[0]
                    and displacement[3] > internal_high
                    and displacement[3] > sweep[1]):
                return StrategyDecision(
                    self.strategy_id, "WAIT",
                    "جابجایی صعودی با بسته‌شدن بدنه بالای سقف داخلی و کندل sweep تأیید نشده است."
                )
            fvg_lower, fvg_upper = sweep[1], fvg[2]
            if fvg_lower >= fvg_upper:
                return StrategyDecision(
                    self.strategy_id, "WAIT",
                    "شکاف ارزش منصفانهٔ صعودی (FVG) بین کندل sweep و کندل سوم تشکیل نشده است."
                )
            if not (retest[2] <= fvg_upper and retest[2] >= fvg_lower
                    and retest[3] > fvg_upper and retest[3] > retest[0]):
                return StrategyDecision(
                    self.strategy_id, "WAIT",
                    "قیمت به FVG برنگشته و با کندل صعودی بالای ناحیه بسته نشده است."
                )

            entry, stop = retest[3], sweep[2]
            risk = entry - stop
            if risk <= 0:
                raise ValueError("structural stop is not below entry")
            target = _target_for_minimum_reward_risk(
                entry, stop, self.minimum_reward_risk,
                expected_entry_slippage_rate=market_snapshot.get(
                    "expected_entry_slippage_rate", DEFAULT_ENTRY_SLIPPAGE_RATE
                ),
            )
            if not all(isfinite(value) for value in (entry, stop, target)):
                raise ValueError("non-finite risk levels")
        except ValueError as exc:
            return StrategyDecision(
                self.strategy_id, "WAIT",
                f"داده یا شواهد ICT معتبر نیست؛ ورود مسدود شد: {exc}"
            )

        return StrategyDecision(
            self.strategy_id, "SIGNAL",
            "جمع‌آوری نقدینگی فروش، جابجایی صعودی، FVG و بازآزمایی تأیید شدند؛ فقط سیگنال Paper.",
            {
                "entry_reference": entry,
                "stop_loss": stop,
                "take_profit": target,
                "minimum_reward_risk": self.minimum_reward_risk,
                "sell_side_liquidity": sell_side_liquidity,
                "internal_high": internal_high,
                "fvg_lower": fvg_lower,
                "fvg_upper": fvg_upper,
                "sweep_timestamp": sweep[4],
                "displacement_timestamp": displacement[4],
                "fvg_confirmation_timestamp": fvg[4],
                "retest_timestamp": retest[4],
                "execution": "paper_only",
            },
        )
