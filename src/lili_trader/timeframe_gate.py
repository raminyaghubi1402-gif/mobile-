"""Three-timeframe entry gate for indicator-free price-action trading."""
from dataclasses import dataclass
from math import isfinite
from typing import Optional


@dataclass(frozen=True)
class GateDecision:
    status: str
    allowed: bool
    direction: Optional[str]
    reason: str


def evaluate_timeframe_gate(hourly, fifteen_minute, five_minute, latest_15m_close):
    """1h sets direction; 15m validates/pulls back; 5m confirms the trigger.

    Each trend may be a TrendState-like object or a mapping with direction and
    last_two_swing_highs/last_two_swing_lows. No indicator values are accepted.
    """
    def field(state, name, default=None):
        return state.get(name, default) if isinstance(state, dict) else getattr(state, name, default)

    if hourly is None:
        return GateDecision("blocked", False, None, "روند یک‌ساعته در دسترس نیست.")
    direction = field(hourly, "direction")
    if direction not in ("up", "down"):
        return GateDecision("blocked", False, None, "روند یک‌ساعته رنج یا نامشخص است؛ معامله ممنوع.")

    if fifteen_minute is None or five_minute is None:
        return GateDecision("wait", False, direction, "داده ۱۵ یا ۵ دقیقه کامل نیست.")

    # A confirmed 15m close through the latest 1h structural swing invalidates
    # the current setup; a normal counter-move without this break is a pullback.
    try:
        close = float(latest_15m_close)
    except (TypeError, ValueError):
        close = float("nan")
    if not isfinite(close) or close <= 0:
        return GateDecision("wait", False, direction, "قیمت بسته‌شدن کندل ۱۵ دقیقه معتبر نیست.")
    if direction == "up":
        lows = field(hourly, "last_two_swing_lows")
        if not isinstance(lows, (tuple, list)) or len(lows) != 2:
            return GateDecision("blocked", False, direction, "کف‌های ساختاری یک‌ساعته معتبر نیستند.")
        try:
            valid_lows = all(isfinite(float(x)) and float(x) > 0 for x in lows)
        except (TypeError, ValueError, OverflowError):
            valid_lows = False
        if not valid_lows:
            return GateDecision("blocked", False, direction, "مقادیر کف ساختاری یک‌ساعته نامعتبرند.")
        if close < lows[-1]:
            return GateDecision("invalidated", False, direction,
                                "ساختار صعودی یک‌ساعته با بسته‌شدن ۱۵ دقیقه زیر آخرین کف تأییدشده شکسته است.")
    else:
        highs = field(hourly, "last_two_swing_highs")
        if not isinstance(highs, (tuple, list)) or len(highs) != 2:
            return GateDecision("blocked", False, direction, "سقف‌های ساختاری یک‌ساعته معتبر نیستند.")
        try:
            valid_highs = all(isfinite(float(x)) and float(x) > 0 for x in highs)
        except (TypeError, ValueError, OverflowError):
            valid_highs = False
        if not valid_highs:
            return GateDecision("blocked", False, direction, "مقادیر سقف ساختاری یک‌ساعته نامعتبرند.")
        if close > highs[-1]:
            return GateDecision("invalidated", False, direction,
                                "ساختار نزولی یک‌ساعته با بسته‌شدن ۱۵ دقیقه بالای آخرین سقف تأییدشده شکسته است.")

    setup_direction = field(fifteen_minute, "direction")
    if setup_direction == ("down" if direction == "up" else "up"):
        return GateDecision("pullback", False, direction,
                            "حرکت ۱۵ دقیقه خلاف روند اصلی، اصلاح محسوب می‌شود؛ تا پایان اصلاح صبر کن.")
    if setup_direction != direction:
        return GateDecision("wait_setup", False, direction,
                            "ساختار ۱۵ دقیقه هنوز ادامه روند اصلی را تأیید نکرده است.")

    trigger_direction = field(five_minute, "direction")
    if trigger_direction != direction:
        return GateDecision("wait_trigger", False, direction,
                            "جهت ۵ دقیقه هنوز ورود هم‌جهت را تأیید نکرده است.")
    return GateDecision("ready", True, direction,
                        "روند ۱ ساعته، ادامه حرکت ۱۵ دقیقه و ماشه ۵ دقیقه هم‌جهت‌اند؛ فقط سناریوی فرضی مجاز است.")
