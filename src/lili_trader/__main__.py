"""CLI for Li-Li Trader. Demo mode is synthetic; Nobitex mode reads public data only."""
import argparse
from datetime import datetime, timedelta, timezone
from .analysis import Candle, analyze_candle


_TEHRAN = timezone(timedelta(hours=3, minutes=30))


def _format_market_time(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, _TEHRAN).strftime("%Y-%m-%d %H:%M")


def main() -> None:
    parser = argparse.ArgumentParser(description="لی‌لی تریدر — تحلیل فقط‌خواندنی")
    parser.add_argument("--nobitex", action="store_true", help="دریافت کندل عمومی از نوبیتکس")
    parser.add_argument("--backtest", action="store_true", help="اجرای بک‌تست قدیمی SMA روی کندل‌های عمومی نوبیتکس")
    parser.add_argument("--price-action-backtest", action="store_true", help="بک‌تست اختصاصی راهبرد ۱h/۱۵m/۵m بدون اندیکاتور")
    parser.add_argument("--sr-diagnostic", action="store_true", help="مقایسهٔ بک‌تست با فیلتر حمایت‌ومقاومت و بدون آن؛ فقط تشخیصی")
    parser.add_argument("--backtest-cash", type=float, default=10.0, help="سرمایهٔ فرضی بک‌تست (پیش‌فرض ۱۰)")
    parser.add_argument("--backtest-currency", choices=("USD", "IRT"), default="USD", help="واحد سرمایهٔ بک‌تست")
    parser.add_argument("--usd-irt-rate", type=float, default=None, help="نرخ مستند تبدیل ۱ USD به IRT")
    parser.add_argument("--symbol", default="BTCIRT", help="نماد نوبیتکس، مثل BTCIRT یا ETHIRT")
    parser.add_argument("--resolution", default="60", help="بازه: 1, 5, 15, 30, 60, 240 یا D")
    parser.add_argument("--count", type=int, default=100, help="تعداد کندل؛ بک‌تست ساختار قیمت تا ۵۰۰۰، سایر حالت‌ها تا ۵۰۰")
    args = parser.parse_args()

    # Price-action backtest operates in the quote currency of Nobitex IRT pairs.
    # Never silently treat USD as IRT: conversion must be explicitly supplied.
    backtest_cash_local = args.backtest_cash
    if args.price_action_backtest:
        if args.backtest_currency == "USD" and args.symbol.upper().endswith("IRT"):
            import math
            if (args.usd_irt_rate is None or args.usd_irt_rate <= 0
                    or not math.isfinite(args.usd_irt_rate)):
                parser.error("برای نمادهای IRT، سرمایهٔ USD به نرخ مثبت و معتبر --usd-irt-rate نیاز دارد؛ نرخ را حدس نمی‌زنیم.")
            backtest_cash_local = args.backtest_cash * args.usd_irt_rate
        elif args.backtest_currency == "IRT":
            backtest_cash_local = args.backtest_cash
        elif args.backtest_currency == "USD":
            parser.error("واحد USD فقط برای نمادهای IRT با نرخ تبدیل صریح پشتیبانی می‌شود؛ واحد بازار را مشخص کنید.")

    if args.price_action_backtest:
        from pathlib import Path
        from .history_store import load_history_csv, merge_candle_history
        from .nobitex_data import NobitexAdapter
        from .price_action_backtest import run_three_timeframe_backtest
        from .timeframe_resample import extend_fifteen_with_five
        adapter = NobitexAdapter()
        history_dir = Path(__file__).resolve().parents[2] / "data" / "history"

        def load_and_refresh(resolution: str, count: int):
            path = history_dir / f"{args.symbol}_{resolution}.csv"
            stored = load_history_csv(path, symbol=args.symbol, resolution=resolution)
            fresh = adapter.fetch_history(args.symbol, resolution, count)
            return merge_candle_history(stored, fresh, max_rows=20_000)

        # Stored daily-collected history extends beyond the API's short recent window.
        hourly = load_and_refresh("60", min(args.count, 2000))
        native_fifteen = load_and_refresh("15", min(args.count, 2000))
        five = load_and_refresh("5", args.count)
        fifteen = extend_fifteen_with_five(native_fifteen, five)
        result = run_three_timeframe_backtest(
            hourly, fifteen, five, starting_cash=backtest_cash_local
        )
        if args.sr_diagnostic:
            baseline = run_three_timeframe_backtest(
                hourly, fifteen, five, starting_cash=backtest_cash_local,
                use_support_resistance_filter=False
            )
            print("مقایسهٔ تشخیصی SR — داده و تنظیمات یکسان؛ این مقایسه پیش‌بینی سود نیست")
            print(f"با فیلتر SR: معاملات={result.trade_count} | خالص={result.net_profit:,.2f} | بازده={result.return_pct:.2f}% | افت سرمایه={result.max_drawdown_pct:.2f}%")
            print(f"بدون فیلتر SR: معاملات={baseline.trade_count} | خالص={baseline.net_profit:,.2f} | بازده={baseline.return_pct:.2f}% | افت سرمایه={baseline.max_drawdown_pct:.2f}%")
            print("تفاوت خالص (با فیلتر منهای بدون فیلتر): " + f"{result.net_profit - baseline.net_profit:,.2f}")
        print("لی‌لی تریدر — بک‌تست ساختار قیمت ۱h / ۱۵m / ۵m (فقط شبیه‌سازی)")
        derived_fifteen = max(0, len(fifteen) - len(native_fifteen))
        print(f"نماد: {args.symbol} | کندل‌ها: ۱h={len(hourly)}، ۱۵m={len(fifteen)} (نوبیتکس={len(native_fifteen)}، تکمیل‌شده از ۵m={derived_fifteen})، ۵m={len(five)}")
        for label, rows in (("۱h", hourly), ("۱۵m", fifteen), ("۵m", five)):
            print(f"بازهٔ {label}: {_format_market_time(rows[0].timestamp)} تا {_format_market_time(rows[-1].timestamp)}")
        overlap_start = max(hourly[0].timestamp, fifteen[0].timestamp, five[0].timestamp)
        overlap_end = min(hourly[-1].timestamp + 3600,
                          fifteen[-1].timestamp + 900, five[-1].timestamp + 300)
        if overlap_end > overlap_start:
            overlap_days = (overlap_end - overlap_start) / 86400
            print(f"بازهٔ مشترک سه تایم‌فریم: {_format_market_time(overlap_start)} تا {_format_market_time(overlap_end)} ({overlap_days:.1f} روز)")
        else:
            print("هشدار: سه تایم‌فریم بازهٔ زمانی مشترک ندارند.")
        print(f"سرمایهٔ مبنا: {args.backtest_cash:,.2f} {args.backtest_currency} | نرخ USD→IRT: {args.usd_irt_rate or 'ندارد'} | سرمایهٔ محاسباتی: {result.starting_cash:,.2f} IRT")
        if args.backtest_currency == "USD" and args.symbol.upper().endswith("IRT"):
            print("توجه: نرخ تبدیل ورودی کاربر است؛ منبع و زمان نرخ باید در گزارش آزمایش ثبت شود.")
        print(f"سود/زیان خالص: {result.net_profit:,.2f} ({result.return_pct:.2f}٪)")
        print(f"بیشترین افت سرمایه: {result.max_drawdown_pct:.2f}٪")
        print(f"نرخ برد: {result.win_rate_pct:.2f}٪ | معاملات: {result.trade_count} (BUY={result.buy_count}, SELL={result.sell_count})")
        pf = "نامشخص" if result.profit_factor is None else ("بی‌نهایت/بدون زیان" if result.profit_factor == float("inf") else f"{result.profit_factor:.2f}")
        print(f"ضریب سود: {pf}")
        print("وضعیت دروازه‌ها: " + ", ".join(f"{key}={value}" for key, value in result.gate_counts))
        for side in ("BUY", "SELL"):
            group = [trade for trade in result.trades if trade.direction == side]
            if group:
                wins = sum(trade.net_pnl > 0 for trade in group)
                net = sum(trade.net_pnl for trade in group)
                print(f"جمع‌بندی {side}: تعداد={len(group)} | برد={wins} | باخت={len(group)-wins} | خالص={net:,.2f}")
        for reason in sorted({trade.exit_reason for trade in result.trades}):
            group = [trade for trade in result.trades if trade.exit_reason == reason]
            print(f"خروج {reason}: تعداد={len(group)} | خالص={sum(t.net_pnl for t in group):,.2f}")
        for index, trade in enumerate(result.trades, 1):
            print(
                f"معاملهٔ {index}: {trade.direction} | "
                f"ورود {_format_market_time(trade.entry_timestamp)} @ {trade.entry_price:,.2f} | "
                f"خروج {_format_market_time(trade.exit_timestamp)} @ {trade.exit_price:,.2f} | "
                f"خالص {trade.net_pnl:,.2f} | علت {trade.exit_reason}"
            )
        for warning in result.warnings:
            print(f"هشدار: {warning}")
        return

    if args.nobitex or args.backtest:
        from .nobitex_data import NobitexAdapter
        candles = NobitexAdapter().fetch_recent(args.symbol, args.resolution, args.count)
        if args.backtest:
            from .backtest import run_sma_backtest
            result = run_sma_backtest(candles, starting_cash=args.backtest_cash)
            print("لی‌لی تریدر — نتیجهٔ بک‌تست تاریخی (فقط شبیه‌سازی)")
            print(f"نماد: {args.symbol} | بازه: {args.resolution} | تعداد کندل: {len(candles)}")
            print(f"سرمایهٔ مبنا: {args.backtest_cash:,.2f} {args.backtest_currency} | سرمایهٔ محاسباتی: {result.starting_cash:,.2f}")
            print(f"ارزش پایانی: {result.ending_equity:,.2f}")
            print(f"سود/زیان خالص: {result.net_profit:,.2f} ({result.return_pct:.2f}٪)")
            print(f"بیشترین افت سرمایه: {result.max_drawdown_pct:.2f}٪")
            print(f"نرخ معاملات سودده: {result.win_rate_pct:.2f}٪ | تعداد معاملات: {result.trade_count}")
            pf = "نامشخص" if result.profit_factor is None else ("بی‌نهایت/بدون معاملهٔ زیان‌ده" if result.profit_factor == float("inf") else f"{result.profit_factor:.2f}")
            print(f"ضریب سود: {pf}")
            for warning in result.warnings:
                print(f"هشدار: {warning}")
        else:
            from .nobitex_report import format_nobitex_report
            print(format_nobitex_report(candles))
        return

    # Demo values only; these are not live market prices.
    sample = Candle(open=100.0, high=106.0, low=98.0, close=104.0)
    print("لی‌لی تریدر — نمونهٔ آموزشی (دادهٔ ساختگی، نه بازار زنده)")
    for key, value in analyze_candle(sample).items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
