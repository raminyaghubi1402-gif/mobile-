"""Research-only trend-pullback engine with closed-candle safeguards.

This engine is intentionally not wired into Paper execution. It produces an
auditable candidate signal only; no orders or position sizing are performed.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import isfinite

from .market_structure import analyze_market_structure
from .price_action_trend import classify_price_action_trend
from .strategy_engines import StrategyDecision

_INTERVAL_SECONDS = {"1h": 3600, "15m": 900, "5m": 300}
_MIN_CANDLES = {"1h": 9, "15m": 7, "5m": 7}


def _closed_series(snapshot: Mapping, timeframe: str) -> tuple[list[dict], int]:
    series = snapshot.get("candles")
    if not isinstance(series, Mapping):
        raise ValueError("snapshot candles must be a timeframe mapping")
    rows = series.get(timeframe)
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise ValueError(f"{timeframe} candles must be a sequence")
    if len(rows) < _MIN_CANDLES[timeframe]:
        raise ValueError(f"insufficient {timeframe} closed candles")

    as_of = snapshot.get("as_of_timestamp")
    if isinstance(as_of, bool) or not isinstance(as_of, int) or as_of < 0:
        raise ValueError("as_of_timestamp must be a non-negative integer")
    interval = _INTERVAL_SECONDS[timeframe]
    normalized = []
    previous_timestamp = None
    for raw in rows:
        if not isinstance(raw, Mapping):
            raise ValueError("each candle must be a mapping")
        if raw.get("is_closed") is not True:
            raise ValueError("only explicitly closed candles are accepted")
        timestamp = raw.get("timestamp")
        if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp < 0:
            raise ValueError("candle timestamp must be a non-negative integer")
        if previous_timestamp is not None and timestamp <= previous_timestamp:
            raise ValueError("candle timestamps must be strictly increasing")
        previous_timestamp = timestamp
        close_timestamp = timestamp + interval
        if close_timestamp > as_of:
            raise ValueError("candle is not closed as of snapshot timestamp")
        if as_of - close_timestamp > interval:
            raise ValueError(f"latest {timeframe} candle is stale")
        candle = dict(raw)
        normalized.append(candle)
    return normalized, as_of


class TrendPullbackEngine:
    """H1 direction -> M15 pullback reversal CHoCH -> M5 fresh BOS.

    Setup/trigger requirements are specific to this engine. It does not call
    the legacy three-timeframe gate, and it cannot place or manage trades.
    """

    strategy_id = "trend_pullback"
    enabled = True
    version = "0.1.0-research"

    def evaluate(self, market_snapshot: Mapping) -> StrategyDecision:
        if not isinstance(market_snapshot, Mapping):
            raise ValueError("market_snapshot must be a mapping")
        try:
            hourly, as_of = _closed_series(market_snapshot, "1h")
            fifteen, _ = _closed_series(market_snapshot, "15m")
            five, _ = _closed_series(market_snapshot, "5m")
        except ValueError as exc:
            return StrategyDecision(
                self.strategy_id, "WAIT", f"داده نامعتبر یا ناکافی: {exc}",
                {"data_validation": "failed"},
            )

        trend = classify_price_action_trend(hourly, pivot_window=2, min_swings=2)
        direction = trend.direction
        if direction not in ("up", "down"):
            return StrategyDecision(
                self.strategy_id, "NO_SETUP",
                "جهت ۱ساعته با ساختار HH/HL یا LH/LL تأیید نشده است؛ ورود مجاز نیست.",
                {"h1_direction": direction, "h1_reason": trend.reason},
            )

        protected_level = (
            trend.last_two_swing_lows[-1] if direction == "up"
            else trend.last_two_swing_highs[-1]
        )
        m15 = analyze_market_structure(fifteen, pivot_window=2)
        m5 = analyze_market_structure(five, pivot_window=2)
        aligned_choch = "CHOCH_UP" if direction == "up" else "CHOCH_DOWN"
        aligned_bos = "BOS_UP" if direction == "up" else "BOS_DOWN"
        m15_event = m15.last_event
        m5_event = m5.last_event

        if direction == "up" and fifteen[-1]["close"] <= protected_level:
            return StrategyDecision(
                self.strategy_id, "INVALIDATED",
                "اصلاح ۱۵دقیقه‌ای کف محافظت‌شدهٔ ۱ساعته را شکسته؛ ستاپ باطل است.",
                {"direction": direction, "h1_protected_low": protected_level,
                 "m15_close": fifteen[-1]["close"]},
            )
        if direction == "down" and fifteen[-1]["close"] >= protected_level:
            return StrategyDecision(
                self.strategy_id, "INVALIDATED",
                "اصلاح ۱۵دقیقه‌ای سقف محافظت‌شدهٔ ۱ساعته را شکسته؛ ستاپ باطل است.",
                {"direction": direction, "h1_protected_high": protected_level,
                 "m15_close": fifteen[-1]["close"]},
            )

        # The M15 CHoCH must be recent (within the last two closed bars) and
        # followed by the M5 BOS after that M15 bar has closed. This avoids
        # treating simultaneous or older events as an entry trigger.
        m15_recent = bool(
            m15_event and m15_event.kind == aligned_choch
            and m15.bias == direction
            and m15_event.candle_index >= len(fifteen) - 2
            and m15_event.timestamp is not None
        )
        if not m15_recent:
            return StrategyDecision(
                self.strategy_id, "WAIT",
                "پایان اصلاح در ۱۵دقیقه با CHoCH هم‌جهت هنوز تأیید نشده است.",
                {"h1_direction": direction, "m15_bias": m15.bias,
                 "m15_last_event": getattr(m15_event, "kind", None)},
            )

        m5_fresh_bos = bool(
            m5_event and m5_event.kind == aligned_bos
            and m5.bias == direction
            and m5_event.candle_index == len(five) - 1
            and m5_event.timestamp is not None
            and m5_event.timestamp >= m15_event.timestamp + _INTERVAL_SECONDS["15m"]
            and m5_event.timestamp + _INTERVAL_SECONDS["5m"] <= as_of
        )
        if not m5_fresh_bos:
            return StrategyDecision(
                self.strategy_id, "WAIT",
                "ماشهٔ ۵دقیقه‌ای BOS تازه پس از بسته‌شدن ستاپ ۱۵دقیقه هنوز تأیید نشده است.",
                {"h1_direction": direction, "m15_choch": m15_event.kind,
                 "m5_bias": m5.bias, "m5_last_event": getattr(m5_event, "kind", None)},
            )

        latest_close = five[-1]["close"]
        return StrategyDecision(
            self.strategy_id, "SIGNAL",
            "ستاپ اصلاح روند تأیید شد؛ این فقط سیگنال پژوهشی است و سفارش/مدیریت موقعیت انجام نمی‌شود.",
            {
                "strategy_version": self.version,
                "direction": "BUY" if direction == "up" else "SELL",
                "h1_trend": direction,
                "h1_protected_level": protected_level,
                "m15_choch": m15_event.kind,
                "m15_choch_timestamp": m15_event.timestamp,
                "m5_bos": m5_event.kind,
                "m5_bos_timestamp": m5_event.timestamp,
                "signal_candle_timestamp": five[-1]["timestamp"],
                "reference_close": latest_close,
                "as_of_timestamp": as_of,
                "entry_order": None,
                "stop_loss": None,
                "take_profit": None,
            },
        )
