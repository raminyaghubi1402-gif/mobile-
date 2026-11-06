"""Format validated market data in Persian; never imply certainty."""
from .market_data import MarketCandle


def format_market_report(candle: MarketCandle) -> str:
    from datetime import datetime, timezone
    fetched = datetime.fromtimestamp(candle.fetched_at_ms / 1000, tz=timezone.utc).isoformat()
    opened = datetime.fromtimestamp(candle.open_time_ms / 1000, tz=timezone.utc).isoformat()
    return (
        f"گزارش لی‌لی تریدر — دادهٔ دریافت‌شده\n"
        f"نماد: {candle.symbol}\nبازه: {candle.interval}\n"
        f"منبع: {candle.source}\nزمان دریافت (UTC): {fetched}\n"
        f"زمان شروع کندل (UTC): {opened}\n"
        f"بازشدن: {candle.open}\nبیشینه: {candle.high}\n"
        f"کمینه: {candle.low}\nبسته‌شدن: {candle.close}\n"
        f"حجم: {candle.volume}\n\n"
        "توجه: این فقط خلاصهٔ دادهٔ بازار است، نه سیگنال یا توصیهٔ معامله. "
        "ممکن است داده با تأخیر همراه باشد."
    )
