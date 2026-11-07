"""Three-timeframe price-action gate: bias, pullback location, then structure trigger."""
from dataclasses import dataclass
from math import isfinite
from typing import Optional


@dataclass(frozen=True)
class GateDecision:
    status: str
    allowed: bool
    direction: Optional[str]
    reason: str


def evaluate_timeframe_gate(
    hourly,
    fifteen_minute,
    five_minute,
    latest_15m_close,
    *,
    setup_zone_touched=False,
    previous_5m_close=None,
    latest_5m_close=None,
    require_entry_confirmation=True,
    five_minute_structure=None,
    five_minute_candle_count=None,
    require_structure_confirmation=False,
):
    """Use 1h for bias, 15m for setup, and a confirmed 5m swing break for entry.

    The 15m trend is allowed to oppose the 1h trend during a pullback. It is
    never allowed to authorize a counter-trend trade. A setup is actionable
    only at a confirmed 1h/15m support or resistance zone and after a 5m close
    crosses the latest confirmed minor swing.
    """
    def field(state, name, default=None):
        return state.get(name, default) if isinstance(state, dict) else getattr(state, name, default)

    def valid_pair(values):
        if not isinstance(values, (tuple, list)) or len(values) != 2:
            return False
        try:
            return all(isfinite(float(value)) and float(value) > 0 for value in values)
        except (TypeError, ValueError, OverflowError):
            return False

    if hourly is None:
        return GateDecision("blocked", False, None, "روند یک‌ساعته در دسترس نیست.")
    direction = field(hourly, "direction")
    if direction not in ("up", "down"):
        return GateDecision("blocked", False, None, "روند یک‌ساعته رنج یا نامشخص است؛ معامله ممنوع.")
    if fifteen_minute is None or five_minute is None:
        return GateDecision("wait", False, direction, "داده ۱۵ یا ۵ دقیقه کامل نیست.")

    try:
        close_15m = float(latest_15m_close)
    except (TypeError, ValueError, OverflowError):
        close_15m = float("nan")
    if not isfinite(close_15m) or close_15m <= 0:
        return GateDecision("wait", False, direction, "قیمت بسته‌شدن کندل ۱۵ دقیقه معتبر نیست.")

    if direction == "up":
        hourly_pivots = field(hourly, "last_two_swing_lows")
        if not valid_pair(hourly_pivots):
            return GateDecision("blocked", False, direction, "کف‌های ساختاری یک‌ساعته معتبر نیستند.")
        if close_15m < float(hourly_pivots[-1]):
            return GateDecision("invalidated", False, direction,
                                "ساختار صعودی یک‌ساعته با بسته‌شدن ۱۵ دقیقه زیر آخرین کف تأییدشده شکسته است.")
    else:
        hourly_pivots = field(hourly, "last_two_swing_highs")
        if not valid_pair(hourly_pivots):
            return GateDecision("blocked", False, direction, "سقف‌های ساختاری یک‌ساعته معتبر نیستند.")
        if close_15m > float(hourly_pivots[-1]):
            return GateDecision("invalidated", False, direction,
                                "ساختار نزولی یک‌ساعته با بسته‌شدن ۱۵ دقیقه بالای آخرین سقف تأییدشده شکسته است.")

    setup_direction = field(fifteen_minute, "direction")
    if setup_direction not in ("up", "down"):
        return GateDecision("wait_setup", False, direction,
                            "ساختار ۱۵ دقیقه رنج یا نامشخص است؛ ستاپ معتبر نداریم.")
    is_pullback = setup_direction != direction

    # The historical backtester can explicitly request its pre-existing
    # trend-alignment model while its own entry simulator is migrated.
    # The live/read-only market analyst always uses strict confirmation.
    if not require_entry_confirmation:
        if is_pullback:
            return GateDecision("pullback", False, direction,
                                "حرکت ۱۵ دقیقه خلاف روند اصلی، اصلاح است؛ مدل قدیمی بک‌تست منتظر هم‌جهتی می‌ماند.")
        if field(five_minute, "direction") != direction:
            return GateDecision("wait_trigger", False, direction,
                                "جهت ۵ دقیقه هنوز ورود هم‌جهت را تأیید نکرده است.")
        return GateDecision("ready_legacy", True, direction,
                            "هم‌جهتی ساختارها طبق مدل قدیمی بک‌تست؛ فاقد فیلتر سخت‌گیرانه ناحیه و شکست ساختار.")

    if not setup_zone_touched:
        return GateDecision("wait_zone", False, direction,
                            "قیمت هنوز به ناحیه معتبر حمایت/مقاومت یک‌ساعته یا ۱۵ دقیقه نرسیده؛ وسط محدوده ورود ممنوع.")

    trigger_direction = field(five_minute, "direction")
    if trigger_direction != direction:
        return GateDecision("wait_trigger", False, direction,
                            "ساختار ۵ دقیقه هنوز به جهت روند اصلی برنگشته است.")

    try:
        previous_close = float(previous_5m_close)
        current_close = float(latest_5m_close)
    except (TypeError, ValueError, OverflowError):
        previous_close, current_close = float("nan"), float("nan")
    if not (isfinite(previous_close) and isfinite(current_close)
            and previous_close > 0 and current_close > 0):
        return GateDecision("wait_trigger", False, direction,
                            "دو قیمت بسته‌شدن معتبر ۵ دقیقه برای تأیید شکست در دسترس نیست.")

    if direction == "up":
        pivots = field(five_minute, "last_two_swing_highs")
        if not valid_pair(pivots):
            return GateDecision("wait_trigger", False, direction,
                                "سقف‌های نوسانی تأییدشده ۵ دقیقه برای ماشه خرید کافی نیست.")
        trigger_level = float(pivots[-1])
        crossed = previous_close <= trigger_level < current_close
        trigger_name = "شکست سقف نوسانی"
    else:
        pivots = field(five_minute, "last_two_swing_lows")
        if not valid_pair(pivots):
            return GateDecision("wait_trigger", False, direction,
                                "کف‌های نوسانی تأییدشده ۵ دقیقه برای ماشه فروش کافی نیست.")
        trigger_level = float(pivots[-1])
        crossed = previous_close >= trigger_level > current_close
        trigger_name = "شکست کف نوسانی"

    if not crossed:
        return GateDecision("wait_trigger", False, direction,
                            f"بسته‌شدن ۵ دقیقه هنوز {trigger_name} را تأیید نکرده است.")

    # When the independent structure engine is supplied by the market
    # analyst, require its BOS/CHoCH to confirm this exact latest closed bar.
    # This prevents a stale old structure event from authorizing an entry.
    if require_structure_confirmation or five_minute_structure is not None:
        structure_bias = field(five_minute_structure, "bias")
        structure_event = field(five_minute_structure, "last_event")
        expected_events = ("BOS_UP", "CHOCH_UP") if direction == "up" else ("BOS_DOWN", "CHOCH_DOWN")
        event_index = field(structure_event, "candle_index") if structure_event else None
        latest_index = five_minute_candle_count - 1 if isinstance(five_minute_candle_count, int) and five_minute_candle_count > 0 else None
        if (structure_bias != direction or structure_event is None
                or field(structure_event, "kind") not in expected_events
                or latest_index is None or event_index != latest_index):
            return GateDecision(
                "wait_structure", False, direction,
                "شکست قیمت دیده شده، اما BOS/CHoCH مستقل ۵ دقیقه روی آخرین کندل بسته‌شده تأیید نشده است."
            )

    if is_pullback:
        reason = "اصلاح ۱۵ دقیقه در ناحیه معتبر قرار گرفته و ۵ دقیقه با شکست ساختار به جهت روند ۱ ساعته برگشته؛ فقط سناریوی فرضی."
    else:
        reason = "روند ۱ ساعته، ساختار ۱۵ دقیقه، ناحیه معتبر و شکست ساختار ۵ دقیقه هم‌جهت‌اند؛ فقط سناریوی فرضی."
    return GateDecision("ready", True, direction, reason)
