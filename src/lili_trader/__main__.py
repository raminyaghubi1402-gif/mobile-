"""CLI for Li-Li Trader. Demo mode is synthetic; Nobitex mode reads public data only."""
import argparse
from .analysis import Candle, analyze_candle


def main() -> None:
    parser = argparse.ArgumentParser(description="لی‌لی تریدر — تحلیل فقط‌خواندنی")
    parser.add_argument("--nobitex", action="store_true", help="دریافت کندل عمومی از نوبیتکس")
    parser.add_argument("--symbol", default="BTCIRT", help="نماد نوبیتکس، مثل BTCIRT یا ETHIRT")
    parser.add_argument("--resolution", default="60", help="بازه: 1, 5, 15, 30, 60, 240 یا D")
    parser.add_argument("--count", type=int, default=100, help="تعداد کندل (۱ تا ۵۰۰)")
    args = parser.parse_args()

    if args.nobitex:
        from .nobitex_data import NobitexAdapter
        from .nobitex_report import format_nobitex_report
        candles = NobitexAdapter().fetch_recent(args.symbol, args.resolution, args.count)
        print(format_nobitex_report(candles))
        return

    # Demo values only; these are not live market prices.
    sample = Candle(open=100.0, high=106.0, low=98.0, close=104.0)
    print("لی‌لی تریدر — نمونهٔ آموزشی (دادهٔ ساختگی، نه بازار زنده)")
    for key, value in analyze_candle(sample).items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
