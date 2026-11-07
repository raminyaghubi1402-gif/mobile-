"""Persian market-analysis report with candle-close and data-quality checks."""
from datetime import datetime, timedelta, timezone
from .technical_analysis import analyze_closes

_TEHRAN = timezone(timedelta(hours=3, minutes=30))

# Nobitex UDF candle resolutions in seconds. D/2D/3D are calendar-day candles.
_RESOLUTION_SECONDS = {
    "1": 60, "5": 300, "15": 900, "30": 1800, "60": 3600,
    "180": 10800, "240": 14400, "360": 21600, "720": 43200,
    "D": 86400, "2D": 172800, "3D": 259200,
}


def _market_time(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, _TEHRAN).strftime("%Y-%m-%d %H:%M:%S (تهران)")


def _quality_notes(candles, interval_seconds: int) -> list[str]:
    notes = []
    timestamps = [int(c.timestamp) for c in candles]
    if timestamps != sorted(timestamps):
        notes.append("ترتیب زمانی کندل‌ها نامنظم است؛ تحلیل با احتیاط انجام شود.")
    if len(set(timestamps)) != len(timestamps):
        notes.append("کندل تکراری در داده‌ها وجود دارد.")
    if interval_seconds > 0 and len(timestamps) > 1:
        gaps = sum(
            max(0, (timestamps[i] - timestamps[i - 1]) // interval_seconds - 1)
            for i in range(1, len(timestamps))
        )
        if gaps:
            notes.append(f"در تاریخچه دست‌کم {gaps} فاصلهٔ کندلی خالی دیده شد؛ نتایج ممکن است ناقص باشد.")
    fetched_at = max(int(getattr(c, "fetched_at", 0) or 0) for c in candles)
    if fetched_at:
        age = max(0, int(datetime.now(timezone.utc).timestamp()) - fetched_at)
        if age > 300:
            notes.append(f"زمان دریافت داده بیش از {age // 60} دقیقه قبل است؛ داده ممکن است قدیمی باشد.")
    return notes


def format_nobitex_report(candles) -> str:
    if not candles:
        raise ValueError("candles must not be empty")

    # Never use a still-forming candle to calculate technical values.
    resolution = str(candles[-1].resolution)
    interval = _RESOLUTION_SECONDS.get(resolution)
    fetched_at = max(int(getattr(c, "fetched_at", 0) or 0) for c in candles)
    closed = list(candles)
    excluded_live = False
    if interval and fetched_at:
        closed = [c for c in candles if int(c.timestamp) + interval <= fetched_at]
        excluded_live = len(closed) != len(candles)

    if not closed:
        latest = candles[-1]
        return "\n".join([
            "گزارش تحلیل لی‌لی تریدر — نوبیتکس",
            f"نماد: {latest.symbol} | بازه: {resolution}",
            f"منبع: {latest.source}",
            f"آخرین کندل: {_market_time(int(latest.timestamp))}",
            "وضعیت: هنوز کندل بسته‌شده‌ای برای تحلیل در دسترس نیست.",
            "این گزارش توصیهٔ خرید/فروش نیست و هیچ معامله‌ای انجام نمی‌دهد.",
        ])

    closes = [c.close for c in closed]
    result = analyze_closes(closes)
    latest = closed[-1]
    notes = list(result.notes)
    notes.extend(_quality_notes(closed, interval or 0))
    if excluded_live:
        notes.append("کندل جاری و بسته‌نشده از محاسبات حذف شد.")
    if interval is None:
        notes.append("طول این بازه برای تشخیص خودکار کندل جاری شناخته‌شده نیست.")

    lines = [
        "گزارش تحلیل لی‌لی تریدر — نوبیتکس (فقط دادهٔ عمومی)",
        f"نماد: {latest.symbol}",
        f"بازه: {latest.resolution}",
        f"منبع: {latest.source}",
        f"زمان آخرین کندل بسته‌شده: {_market_time(int(latest.timestamp))}",
        f"زمان دریافت داده: {_market_time(fetched_at)}" if fetched_at else "زمان دریافت داده: نامشخص",
        f"کندل بسته‌شدهٔ بررسی‌شده: {result.candle_count}",
        f"آخرین قیمت بسته‌شدن: {result.latest_close:g}",
        f"روند میانگین‌ها (SMA ۷/۲۵): {result.trend}",
        f"SMA کوتاه‌مدت (۷): {result.sma_fast:g}" if result.sma_fast is not None else "SMA کوتاه‌مدت (۷): داده ناکافی",
        f"SMA بلندمدت (۲۵): {result.sma_slow:g}" if result.sma_slow is not None else "SMA بلندمدت (۲۵): داده ناکافی",
        f"RSI (۱۴): {result.rsi14:.2f}" if result.rsi14 is not None else "RSI (۱۴): داده ناکافی",
        "",
        *[f"• {note}" for note in notes],
        "",
        "هشدار: این گزارش آموزشی و تحلیلی است؛ توصیهٔ خرید/فروش نیست و توصیهٔ قطعی مالی هم محسوب نمی‌شود.",
        "دادهٔ عمومی ممکن است ناقص یا با تأخیر باشد. نبود سیگنال معتبر یعنی باید صبر کرد.",
        "این نسخه فقط‌خواندنی است؛ به حساب نوبیتکس وارد نمی‌شود و معامله‌ای انجام نمی‌دهد.",
    ]
    return "\n".join(lines)
