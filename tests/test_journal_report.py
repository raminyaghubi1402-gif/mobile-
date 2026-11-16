import tempfile
import unittest
from pathlib import Path

from lili_trader.journal import TradeJournal, TradeRecord
from lili_trader.journal_report import render_journal_report, summarize_journal


def closed_trade(trade_id, exit_time, gross, fees, slippage, risk):
    return TradeRecord(
        trade_id=trade_id, symbol="BTCIRT", strategy="test", side="long",
        status="closed", entry_time="2026-11-16T09:00:00+00:00",
        entry_price=100, quantity=1, initial_risk_quote=risk,
        entry_reason="test setup", exit_time=exit_time, exit_price=100 + gross,
        gross_pnl_quote=gross, fees_quote=fees, slippage_quote=slippage,
        net_pnl_quote=gross - fees - slippage, exit_reason="test exit",
    )


class JournalReportTests(unittest.TestCase):
    def test_metrics_include_costs_and_use_exit_time_order(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = TradeJournal(Path(directory) / "trades.jsonl")
            # Append out of close-time order to prove the equity curve is sorted.
            journal.append(closed_trade(
                "loss", "2026-11-16T11:00:00+00:00", -4, 1, 1, 3
            ))
            journal.append(closed_trade(
                "win", "2026-11-16T10:00:00+00:00", 12, 1, 1, 5
            ))
            journal.append(closed_trade(
                "flat", "2026-11-16T12:00:00+00:00", 0, 0, 0, 2
            ))
            journal.append(TradeRecord(
                trade_id="open", symbol="ETHIRT", strategy="test", side="long",
                status="open", entry_time="2026-11-16T12:00:00+00:00",
                entry_price=10, quantity=1, initial_risk_quote=1,
                entry_reason="still open",
            ))

            report = summarize_journal(journal, starting_equity=1000)
            self.assertEqual(report.total_records, 4)
            self.assertEqual(report.open_trades, 1)
            self.assertEqual(report.closed_trades, 3)
            self.assertEqual((report.wins, report.losses, report.breakeven), (1, 1, 1))
            self.assertAlmostEqual(report.win_rate_pct, 100 / 3)
            self.assertAlmostEqual(report.net_profit_quote, 4)
            self.assertAlmostEqual(report.gross_pnl_quote, 8)
            self.assertAlmostEqual(report.fees_quote, 2)
            self.assertAlmostEqual(report.slippage_quote, 2)
            self.assertAlmostEqual(report.profit_factor, 10 / 6)
            self.assertAlmostEqual(report.expectancy_quote, 4 / 3)
            self.assertAlmostEqual(report.average_r, 0)
            self.assertAlmostEqual(report.max_drawdown_quote, 6)
            self.assertAlmostEqual(report.max_drawdown_pct, 6 / 1010 * 100)
            self.assertEqual(report.max_losing_streak, 1)
            self.assertEqual(report.ending_equity, 1004)

            rendered = render_journal_report(report)
            self.assertIn("گزارش عملکرد ژورنال", rendered)
            self.assertIn("تضمین سود", rendered)

    def test_empty_journal_has_zero_trade_metrics(self):
        with tempfile.TemporaryDirectory() as directory:
            report = summarize_journal(
                TradeJournal(Path(directory) / "empty.jsonl"),
                starting_equity=100,
            )
            self.assertEqual(report.closed_trades, 0)
            self.assertEqual(report.win_rate_pct, 0)
            self.assertEqual(report.expectancy_quote, 0)
            self.assertIsNone(report.profit_factor)
            self.assertIsNone(report.average_r)
            self.assertIsNone(report.best_trade_net)
            self.assertIsNone(report.worst_trade_net)

    def test_invalid_starting_equity_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = TradeJournal(Path(directory) / "trades.jsonl")
            for value in (True, 0, -1, float("nan"), "100"):
                with self.subTest(value=value):
                    with self.assertRaises(ValueError):
                        summarize_journal(journal, starting_equity=value)


if __name__ == "__main__":
    unittest.main()
