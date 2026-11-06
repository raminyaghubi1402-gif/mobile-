"""CLI for Li-Li Trader. Demo mode is synthetic; Nobitex mode reads public data only."""
import argparse
from .analysis import Candle, analyze_candle


def main() -> None:
    parser = argparse.ArgumentParser(description="لی‌لی تریدر — تحلیل فقط‌خواندنی")
    parser.add_argument("--nobitex", action="store_true", help="دریافت کندل عمومی از نوبیتکس")
    parser.add_argument("--backtest", action="store_true", help="اجرای بک‌تست قدیمی SMA روی کندل‌های عمومی نوبیتکس")
    parser.add_argument("--price-action-backtest", action="store_true", help="بک‌تست اختصاصی راهبرد ۱h/۱۵m/۵m بدون اندیکاتور")
    parser.add_argument("--backtest-cash", type=float, default=1000000.0, help="موجودی فرضی بک‌تست")
    parser.add_argument("--symbol", default="BTCIRT", help="نماد نوبیتکس، مثل BTCIRT یا ETHIRT")
    parser.add_argument("--resolution", default="60", help="بازه: 1, 5, 15, 30, 60, 240 یا D")
    parser.add_argument("--count", type=int, default=100, help="تعداد کندل (۱ تا ۵۰۰)")
    args = parser.parse_args()

    if args.price_action_backtest:
        from .nobitex_data import NobitexAdapter
        from .price_action_backtest import run_three_timeframe_backtest
        adapter = NobitexAdapter()
        # Each timeframe is fetched independently from the public, read-only API.
        hourly = adapter.fetch_recent(args.symbol, "60", args.count)
        fifteen = adapter.fetch_recent(args.symbol, "15", args.count)
        five = adapter.fetch_recent(args.symbol, "5", args.count)
        result = run_three_timeframe_backtest(
            hourly, fifteen, five, starting_cash=args.backtest_cash
        )
        print("لی‌لی تریدر — بک‌تست ساختار قیمت ۱h / ۱۵m / ۵m (فقط شبیه‌سازی)")
        print(f"نماد: {args.symbol} | کندل‌ها: ۱h={len(hourly)}، ۱۵m={len(fifteen)}، ۵m={len(five)}")
        print(f"موجودی آغازین: {result.starting_cash:,.2f} | ارزش پایانی: {result.ending_equity:,.2f}")
        print(f"سود/زیان خالص: {result.net_profit:,.2f} ({result.return_pct:.2f}٪)")
        print(f"بیشترین افت سرمایه: {result.max_drawdown_pct:.2f}٪")
        print(f"نرخ برد: {result.win_rate_pct:.2f}٪ | معاملات: {result.trade_count} (BUY={result.buy_count}, SELL={result.sell_count})")
        pf = "نامشخص" if result.profit_factor is None else ("بی‌نهایت/بدون زیان" if result.profit_factor == float("inf") else f"{result.profit_factor:.2f}")
        print(f"ضریب سود: {pf}")
        print("وضعیت دروازه‌ها: " + ", ".join(f"{key}={value}" for key, value in result.gate_counts))
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
            print(f"موجودی آغازین: {result.starting_cash:,.2f}")
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
