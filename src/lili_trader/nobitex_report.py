"""Persian analysis report from Nobitex public candles."""
from datetime import datetime, timezone
from .technical_analysis import analyze_closes


def format_nobitex_report(candles) -> str:
    if not candles:
        raise ValueError("candles must not be empty")
    closes = [c.close for c in candles]
    result = analyze_closes(closes)
    latest = candles[-1]
    timestamp = datetime.fromtimestamp(latest.timestamp, tz=timezone.utc).isoformat()
    lines = [
        "گزارش تحلیل لی‌لی تریدر — نوبیتکس",
        f"نماد: {latest.symbol}",
        f"بازه: {latest.resolution}",
        f"منبع: {latest.source}",
        f"آخرین زمان کندل (UTC): {timestamp}",
        f"تعداد کندل بررسی‌شده: {result.candle_count}",
        f"آخرین قیمت بسته‌شدن: {result.latest_close:g}",
        f"روند بر اساس میانگین‌ها: {result.trend}",
        f"SMA کوتاه‌مدت (۷): {result.sma_fast:g}" if result.sma_fast is not None else "SMA کوتاه‌مدت (۷): داده ناکافی",
        f"SMA بلندمدت (۲۵): {result.sma_slow:g}" if result.sma_slow is not None else "SMA بلندمدت (۲۵): داده ناکافی",
        f"RSI (۱۴): {result.rsi14:.2f}" if result.rsi14 is not None else "RSI (۱۴): داده ناکافی",
        "",
        *[f"• {note}" for note in result.notes],
        "",
        "هشدار: این گزارش آموزشی و صرفاً تحلیلی است؛ توصیهٔ خرید/فروش نیست.",
        "دادهٔ عمومی ممکن است تأخیر یا نقص داشته باشد. پیش از هر تصمیم، منبع و زمان داده را بررسی کنید.",
        "این نسخه هیچ معامله‌ای انجام نمی‌دهد و به حساب نوبیتکس وارد نمی‌شود.",
    ]
    return "\n".join(lines)
