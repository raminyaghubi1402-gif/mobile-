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
    parser.add_argument("--market-analysis", action="store_true", help="تحلیل چندتایم‌فریمی حرفه‌ای با دادهٔ تازهٔ نوبیتکس (فقط‌خواندنی)")
    parser.add_argument("--backtest", action="store_true", help="اجرای بک‌تست قدیمی SMA روی کندل‌های عمومی نوبیتکس")
    parser.add_argument("--price-action-backtest", action="store_true", help="بک‌تست اختصاصی راهبرد ۱h/۱۵m/۵m بدون اندیکاتور")
    parser.add_argument("--spot-only", action="store_true", help="اجبار به معاملات نقدی؛ فروش استقراضی در بک‌تست ممنوع")
    parser.add_argument("--sr-diagnostic", action="store_true", help="مقایسهٔ بک‌تست با فیلتر حمایت‌ومقاومت و بدون آن؛ فقط تشخیصی")
    parser.add_argument("--backtest-cash", type=float, default=10.0, help="سرمایهٔ فرضی بک‌تست (پیش‌فرض ۱۰)")
    parser.add_argument("--backtest-currency", choices=("USD", "IRT"), default="USD", help="واحد سرمایهٔ بک‌تست")
    parser.add_argument("--usd-irt-rate", type=float, default=None, help="نرخ مستند تبدیل ۱ USD به IRT")
    parser.add_argument("--usd-irt-rate-source", default=None, help="منبع نرخ تبدیل USD/IRT برای ثبت در دفتر ممیزی")
    parser.add_argument("--usd-irt-rate-at", default=None, help="زمان دریافت نرخ تبدیل با ISO-8601 و منطقهٔ زمانی")
    parser.add_argument("--symbol", default="BTCIRT", help="نماد نوبیتکس، مثل BTCIRT یا ETHIRT")
    parser.add_argument("--resolution", default="60", help="بازه: 1, 5, 15, 30, 60, 240 یا D")
    parser.add_argument("--count", type=int, default=100, help="تعداد کندل؛ بک‌تست ساختار قیمت تا ۵۰۰۰، سایر حالت‌ها تا ۵۰۰")
    parser.add_argument("--decision-journal", default=None, metavar="PATH", help="ثبت تمام تصمیم‌های هر کندل بسته‌شدهٔ ۵ دقیقه در دفتر ممیزی")
    parser.add_argument("--decision-journal-report", default=None, metavar="PATH", help="گزارش تصمیم‌های BUY/SELL/WAIT و بازبینی خطاها")
    parser.add_argument("--journal-report", default=None, metavar="PATH", help="گزارش عملکرد ژورنال معاملات آزمایشی JSONL")
    parser.add_argument("--journal-starting-equity", type=float, default=None, help="سرمایهٔ شروع برای محاسبهٔ افت سرمایهٔ ژورنال")
    parser.add_argument("--backtest-journal", default=None, metavar="PATH", help="ثبت معاملات بسته‌شدهٔ بک‌تست ساختار قیمت در ژورنال JSONL")
    parser.add_argument("--ai-review", action="store_true", help="بازبینی تحلیل قطعی با عامل تحلیل، منتقد و جمع‌بندی مدل‌های پیکربندی‌شده")
    parser.add_argument("--paper-forward", action="store_true", help="اجرای یک نوبت پایش و شبیه‌سازی زندهٔ Paper فقط برای BTC/ETH نوبیتکس")
    parser.add_argument("--forward-state-dir", default="data/paper/forward", help="مسیر پایدار حساب و دفتر Paper")
    parser.add_argument("--forward-usd-irt-rate", type=float, default=None, help="نرخ اولیهٔ دستی USD/IRT برای راه‌اندازی حساب ۱۰ دلاری")
    parser.add_argument("--forward-usd-irt-source", default=None, help="منبع نرخ اولیهٔ USD/IRT")
    parser.add_argument("--forward-usd-irt-at", default=None, help="زمان دریافت نرخ اولیه با ISO-8601 و منطقهٔ زمانی")
    parser.add_argument("--forward-max-lag-seconds", type=int, default=180, help="حداکثر عمر کندل اجرایی ۵ دقیقه‌ای")
    args = parser.parse_args()

    if args.paper_forward:
        from .paper_forward import run_forward_cycle
        try:
            report = run_forward_cycle(
                args.forward_state_dir,
                usd_irt_rate=args.forward_usd_irt_rate,
                usd_irt_rate_source=args.forward_usd_irt_source,
                usd_irt_rate_at=args.forward_usd_irt_at,
                max_lag_seconds=args.forward_max_lag_seconds,
            )
        except Exception as exc:
            parser.error(f"چرخهٔ Paper متوقف شد؛ داده یا وضعیت قابل‌اعتماد نیست ({type(exc).__name__}). هیچ سفارش واقعی ارسال نشده است.")
        print("لی‌لی تریدر — پایش و حساب Paper نوبیتکس")
        print(f"وضعیت: {report['status']} | حالت: {report.get('mode', 'در انتظار راه‌اندازی')}")
        print(report.get("message", ""))
        if report["status"] == "ok":
            for symbol, item in report["symbols"].items():
                print(
                    f"{symbol}: تصمیم جدید={item['new_decisions']} | "
                    f"معاملهٔ بستهٔ ثبت‌شده={item['closed_trades_linked']} | "
                    f"موقعیت باز={'بله' if item['open_position'] else 'خیر'} | "
                    f"ارزش تخمینی={item['marked_equity_irt']:,.0f} ریال | "
                    f"افت بازپخش={item['max_drawdown_pct_in_replay']:.2f}%"
                )
            summary = report["journal_summary"]
            print(
                f"دفتر: تصمیم={summary['decisions']} | BUY={summary['buy']} | "
                f"SELL={summary['sell']} | WAIT={summary['wait']} | "
                f"نتیجهٔ بسته={summary['outcomes']}"
            )
        elif report["status"] == "initialized":
            print(f"سرمایهٔ اولیه: {report['initial_capital_usd']} USD | نرخ seed: {report['usd_irt_rate']:,.2f} IRT/USD")
            print("در این نوبت فقط حساب راه‌اندازی شد؛ هیچ سیگنال تاریخی به‌عنوان معامله ثبت نشد.")
        return

    if args.decision_journal_report is not None:
        from .decision_journal import DecisionJournal
        try:
            summary = DecisionJournal(args.decision_journal_report).summary()
            print("گزارش دفتر تصمیم‌ها و بازبینی لی‌لی")
            print(f"تصمیم‌ها: {summary['decisions']} | BUY={summary['buy']} | SELL={summary['sell']} | WAIT={summary['wait']}")
            print(f"نتایج معاملات متصل: {summary.get('outcomes', 0)} | برد: {summary.get('outcome_wins', 0)} | باخت: {summary.get('outcome_losses', 0)} | خالص: {summary.get('outcome_net_pnl_quote', 0):,.2f} IRT")
            print(f"بازبینی انسانی: {summary['reviews']} | بازبینی منفی/خطای اجرا: {summary['bad_reviews']}")
            print("برچسب‌های خطا: " + (", ".join(f"{key}={value}" for key, value in summary["failure_tags"].items()) or "هنوز موردی ثبت نشده"))
            print(f"آخرین اثرانگشت: {summary['last_hash'] or 'دفتر خالی است'}")
        except (OSError, ValueError, TypeError) as exc:
            parser.error(f"گزارش دفتر تصمیم‌ها قابل تولید نیست: {type(exc).__name__}")
        return

    if args.journal_report is not None:
        if args.journal_starting_equity is None:
            parser.error("برای گزارش ژورنال، --journal-starting-equity را صریحاً وارد کنید.")
        from .journal import TradeJournal
        from .journal_report import render_journal_report, summarize_journal
        try:
            summary = summarize_journal(
                TradeJournal(args.journal_report),
                starting_equity=args.journal_starting_equity,
            )
            print(render_journal_report(summary))
        except (OSError, ValueError, TypeError) as exc:
            parser.error(f"گزارش ژورنال قابل تولید نیست: {type(exc).__name__}")
        return

    if args.ai_review and not args.market_analysis:
        parser.error("--ai-review فقط همراه --market-analysis قابل استفاده است.")

    if args.market_analysis:
        try:
            if args.ai_review:
                from .market_analysis_runner import fetch_analysis_with_metadata
                from .ai_router import LiliAIRouter
                from .ai_team import LiliAnalysisTeam
                analysis, source, observed_at = fetch_analysis_with_metadata(
                    args.symbol, count=min(args.count, 500)
                )
                print(analysis.report_fa)
                if not observed_at:
                    parser.error("زمان دریافت داده از منبع قابل تأیید نیست؛ بازبینی هوش مصنوعی اجرا نشد.")
                router = LiliAIRouter.from_env()
                review = LiliAnalysisTeam(router).review(
                    analysis, source=source, observed_at=observed_at
                )
                print("\n" + review.render_fa())
            else:
                from .market_analysis_runner import fetch_and_analyze
                print(fetch_and_analyze(args.symbol, count=min(args.count, 500)))
        except Exception as exc:
            parser.error(f"تحلیل بازار انجام نشد؛ دادهٔ معتبر یا مدل در دسترس نیست ({type(exc).__name__}). هیچ معامله‌ای انجام نشده است.")
        return

    # Price-action backtest operates in the quote currency of Nobitex IRT pairs.
    # Never silently treat USD as IRT: conversion must be explicitly supplied.
    backtest_cash_local = args.backtest_cash
    if args.price_action_backtest:
        if args.backtest_currency == "USD" and args.symbol.upper().endswith("IRT"):
            import math
            if (args.usd_irt_rate is None or args.usd_irt_rate <= 0
                    or not math.isfinite(args.usd_irt_rate)):
                parser.error("برای نمادهای IRT، سرمایهٔ USD به نرخ مثبت و معتبر --usd-irt-rate نیاز دارد؛ نرخ را حدس نمی‌زنیم.")
            if not isinstance(args.usd_irt_rate_source, str) or not args.usd_irt_rate_source.strip():
                parser.error("برای حساب USD، --usd-irt-rate-source الزامی است.")
            if not isinstance(args.usd_irt_rate_at, str) or not args.usd_irt_rate_at.strip():
                parser.error("برای حساب USD، --usd-irt-rate-at با زمان ISO-8601 الزامی است.")
            try:
                rate_time = datetime.fromisoformat(args.usd_irt_rate_at.replace("Z", "+00:00"))
            except ValueError:
                parser.error("زمان نرخ باید ISO-8601 معتبر و دارای منطقهٔ زمانی باشد.")
            if rate_time.tzinfo is None or rate_time.utcoffset() is None:
                parser.error("زمان نرخ باید منطقهٔ زمانی داشته باشد.")
            rate_age = (datetime.now(timezone.utc) - rate_time.astimezone(timezone.utc)).total_seconds()
            if rate_age < -60 or rate_age > 900:
                parser.error("نرخ تبدیل قدیمی یا دارای زمان آینده است؛ نرخ حداکثر ۱۵ دقیقه‌ای لازم است.")
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
        decision_callback = None
        decision_run_id = None
        if args.decision_journal is not None:
            import uuid
            from .decision_journal import DecisionJournal
            if args.symbol not in {"BTCIRT", "ETHIRT"}:
                parser.error("دفتر تصمیم فقط BTCIRT و ETHIRT را می‌پذیرد.")
            decision_journal = DecisionJournal(args.decision_journal)
            decision_run_id = uuid.uuid4().hex[:12]

            def decision_callback(event):
                from .nobitex_limits import (
                    NOBITEX_MINIMUM_SPOT_ORDER_IRT, NOBITEX_MINIMUM_ORDER_SOURCE,
                    NOBITEX_BASE_SPOT_FEE_RATE_IRT, NOBITEX_FEE_SOURCE,
                )
                decision_journal.append_decision(
                    decision_id=event["decision_id"],
                    occurred_at=event["occurred_at"],
                    market_data_at=event["market_data_at"],
                    symbol=event["symbol"],
                    action=event["action"],
                    strategy=event["strategy"],
                    strategy_version=event["strategy_version"],
                    reason=event["reason"],
                    evidence={
                        **event["evidence"],
                        "minimum_order_quote": NOBITEX_MINIMUM_SPOT_ORDER_IRT,
                        "minimum_order_source": NOBITEX_MINIMUM_ORDER_SOURCE,
                        "fee_rate": NOBITEX_BASE_SPOT_FEE_RATE_IRT,
                        "fee_source": NOBITEX_FEE_SOURCE,
                        **({
                            "capital_conversion": {
                                "usd_amount": args.backtest_cash,
                                "usd_irt_rate": args.usd_irt_rate,
                                "rate_source": args.usd_irt_rate_source,
                                "rate_observed_at": args.usd_irt_rate_at,
                            }
                        } if args.backtest_currency == "USD" else {}),
                    },
                    entry_price=event["entry_price"],
                    stop_loss=event["stop_loss"],
                    take_profit=event["take_profit"],
                    risk_quote=event["risk_quote"],
                    timeframe=event["timeframe"],
                    trade_id=event["trade_id"],
                )

        minimum_order_quote = None
        if args.decision_journal is not None:
            from .nobitex_limits import (
                NOBITEX_MINIMUM_SPOT_ORDER_IRT, NOBITEX_BASE_SPOT_FEE_RATE_IRT,
            )
            minimum_order_quote = NOBITEX_MINIMUM_SPOT_ORDER_IRT
            audit_fee_rate = NOBITEX_BASE_SPOT_FEE_RATE_IRT
        else:
            audit_fee_rate = 0.001

        result = run_three_timeframe_backtest(
            hourly, fifteen, five, starting_cash=backtest_cash_local,
            decision_callback=decision_callback,
            decision_run_id=decision_run_id,
            symbol=args.symbol,
            allow_short=(args.decision_journal is None and not args.spot_only),
            minimum_order_quote=minimum_order_quote,
            fee_rate=audit_fee_rate,
        )
        if args.decision_journal is not None:
            from .decision_outcome import append_backtest_outcomes
            try:
                linked_outcomes = append_backtest_outcomes(
                    result, decision_journal, run_id=decision_run_id, symbol=args.symbol
                )
            except (OSError, ValueError, TypeError) as exc:
                parser.error(f"اتصال نتیجهٔ معاملات به تصمیم‌ها انجام نشد: {type(exc).__name__}")
            print(f"نتایج بسته‌شدهٔ متصل‌شده به تصمیم‌ها: {linked_outcomes}")
            summary = decision_journal.summary()
            print(f"دفتر تصمیم‌ها: {args.decision_journal} | شناسه اجرا: {decision_run_id}")
            print(f"تصمیم‌های این دفتر: BUY={summary['buy']} | SELL={summary['sell']} | WAIT={summary['wait']}")
        if args.backtest_journal is not None:
            from .journal import TradeJournal
            from .backtest_journal import append_backtest_result
            try:
                journaled = append_backtest_result(
                    result, TradeJournal(args.backtest_journal), symbol=args.symbol
                )
            except (OSError, ValueError, TypeError) as exc:
                parser.error(f"ثبت معاملات بک‌تست در ژورنال انجام نشد: {type(exc).__name__}")
            print(f"معاملات جدید ثبت‌شده در ژورنال: {journaled} | مسیر: {args.backtest_journal}")
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
            print("توجه: سود/زیان خام در واحد IRT بازار گزارش می‌شود؛ منبع و زمان نرخ تبدیل باید جداگانه در گزارش اجرا ثبت شود.")
        print(f"سود/زیان خالص محاسباتی: {result.net_profit:,.2f} IRT ({result.return_pct:.2f}٪)")
        if args.backtest_currency == "USD" and args.symbol.upper().endswith("IRT"):
            print(f"معادل تقریبی سود/زیان به USD با نرخ ورودی همین اجرا: {result.net_profit / args.usd_irt_rate:,.4f} USD")
            print("توجه: نرخ USDT/IRT فقط پروکسی تقریبی USD/IRT است؛ این معادل ارزش‌گذاری دقیق دلاری نیست.")
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
