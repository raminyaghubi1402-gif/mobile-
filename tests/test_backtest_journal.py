import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from lili_trader.backtest_journal import (
    BacktestJournalError,
    append_backtest_result,
)
from lili_trader.journal import TradeJournal


def result_with_trade(**overrides):
    values = dict(
        direction="BUY",
        entry_timestamp=1_795_000_000,
        exit_timestamp=1_795_003_600,
        entry_price=100.5,
        exit_price=110.5,
        quantity=2.0,
        gross_pnl=22.0,
        fees=2.0,
        net_pnl=20.0,
        exit_reason="smart_stop",
        initial_risk_quote=10.0,
        slippage_quote=1.0,
    )
    values.update(overrides)
    return SimpleNamespace(trades=(SimpleNamespace(**values),))


class BacktestJournalTests(unittest.TestCase):
    def test_imports_closed_trade_with_costs_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = TradeJournal(Path(directory) / "backtest.jsonl")
            result = result_with_trade()
            self.assertEqual(
                append_backtest_result(result, journal, symbol="BTCIRT"), 1
            )
            self.assertEqual(
                append_backtest_result(result, journal, symbol="BTCIRT"), 0
            )
            records = journal.records()
            self.assertEqual(len(records), 1)
            trade = records[0]
            self.assertEqual(trade.status, "closed")
            self.assertEqual(trade.side, "long")
            self.assertEqual(trade.symbol, "BTCIRT")
            self.assertAlmostEqual(trade.gross_pnl_quote, 23.0)
            self.assertAlmostEqual(trade.fees_quote, 2.0)
            self.assertAlmostEqual(trade.slippage_quote, 1.0)
            self.assertAlmostEqual(trade.net_pnl_quote, 20.0)
            self.assertAlmostEqual(trade.r_multiple, 2.0)

    def test_same_trade_identity_with_different_result_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = TradeJournal(Path(directory) / "backtest.jsonl")
            append_backtest_result(result_with_trade(), journal, symbol="BTCIRT")
            changed = result_with_trade(net_pnl=21.0)
            with self.assertRaisesRegex(BacktestJournalError, "different contents"):
                append_backtest_result(changed, journal, symbol="BTCIRT")

    def test_invalid_backtest_trade_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = TradeJournal(Path(directory) / "backtest.jsonl")
            with self.assertRaises(BacktestJournalError):
                append_backtest_result(
                    result_with_trade(direction="UNKNOWN"), journal, symbol="BTCIRT"
                )
            self.assertEqual(journal.records(), ())


if __name__ == "__main__":
    unittest.main()
