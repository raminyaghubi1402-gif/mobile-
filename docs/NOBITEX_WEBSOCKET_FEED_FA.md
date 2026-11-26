# دریافت زندهٔ کندل نوبیتکس در Li-Li Trader

## محدوده
این ماژول فقط کانال‌های عمومی کندل را می‌خواند. هیچ توکن حساب، کانال خصوصی، کیف پول، سفارش یا معامله‌ای استفاده نمی‌شود. دریافت تاریخی HTTP موجود بدون تغییر باقی می‌ماند.

## پروتکل مستندشده
- WebSocket/Centrifugo: `wss://ws.nobitex.ir/connection/websocket`
- اتصال: `{"connect":{},"id":1}`
- اشتراک: `{"id":2,"subscribe":{"channel":"public:candle-BTCIRT-5"}}`
- کانال‌ها: `public:candle-{BTCIRT|ETHIRT}-{5|15|60}`
- فیلدهای کندل: `t,o,h,l,c,v`؛ زمان `t` بر حسب ثانیه Unix است.
- انتشارها تقریباً هر ۱۰ ثانیه انجام می‌شوند؛ هر پیام الزاماً کندل بسته‌شده نیست.
- پیام keepalive خالی `{}` با همان پیام پاسخ داده می‌شود.

منبع رسمی: https://github.com/nobitex/docs-api/blob/master/source/includes/_websocket.md

## اجرا
نیازمند Python 3.11 و بسته `websocket-client` است:

```bash
python -m pip install websocket-client
python scripts/stream_nobitex_candles.py --duration-seconds 300
```

خروجی JSONL در `data/market/live/nobitex-candle-stream.jsonl` ذخیره می‌شود. هر پیام دریافتی یک snapshot مستقل است؛ به‌روزرسانی‌های متعدد با timestamp یکسان برای کندل باز طبیعی‌اند. فیلد `is_closed` از زمان دریافت و پایان بازهٔ کندل محاسبه می‌شود.

## بازیابی و محدودیت
پس از قطع اتصال، کلاینت با تأخیر افزایشی تا سقف ۳۰ ثانیه دوباره وصل و هر شش کانال را subscribe می‌کند. WebSocket به‌تنهایی تضمین بازیابی پیام‌های ازدست‌رفته نیست؛ شکاف باید با API تاریخی و timestampهای واقعی بررسی شود. هیچ کندل مصنوعی ساخته نمی‌شود.

Workflow دستی GitHub Actions نمونه‌گیری محدود تا ۳۶۰۰ ثانیه است، نه سرویس ۲۴/۷. برای دریافت پیوسته باید runner در یک سرویس یا برنامهٔ همیشه‌فعال اجرا شود. اگر هیچ publication نرسد، اسکریپت خروجی موفق اعلام نمی‌کند.
