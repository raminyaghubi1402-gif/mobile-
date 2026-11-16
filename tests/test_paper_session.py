import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lili_trader.journal import JournalError, TradeJournal
from lili_trader.paper import PaperAccount
from lili_trader.paper_session import PaperSessionError, PaperTradeSession


class PaperTradeSessionTests(unittest.TestCase):
    def make_session(self, directory):
        account = PaperAccount(1000, fee_rate=0.01)
        journal = TradeJournal(Path(directory) / "trades.jsonl")
        return account, journal, PaperTradeSession(account, journal)

    def test_open_close_lifecycle_reconciles_fees_slippage_and_net_r(self):
        with tempfile.TemporaryDirectory() as directory:
            account, journal, session = self.make_session(directory)
            opened = session.open_trade(
                trade_id="paper-001", symbol="BTCIRT", strategy="bos_pullback",
                quantity=2, entry_price=100, initial_risk_quote=10,
                entry_reason="confirmed bullish structure",
                entry_time="2026-11-16T10:00:00+00:00",
                entry_slippage_quote=0.5,
            )
            self.assertEqual(opened.status, "open")
            self.assertEqual(account.position, 2)
            self.assertAlmostEqual(opened.fees_quote, 2)

            closed = session.close_trade(
                exit_price=120, exit_reason="target reached",
                exit_time="2026-11-16T11:00:00+00:00",
                exit_slippage_quote=0.3,
            )
            self.assertEqual(closed.status, "closed")
            self.assertAlmostEqual(closed.gross_pnl_quote, 40)
            self.assertAlmostEqual(closed.fees_quote, 4.4)
            self.assertAlmostEqual(closed.slippage_quote, 0.8)
            self.assertAlmostEqual(closed.net_pnl_quote, 34.8)
            self.assertAlmostEqual(closed.r_multiple, 3.48)
            self.assertEqual(account.position, 0)
            self.assertEqual(len(journal.records()), 1)
            self.assertEqual(len(journal.path.read_text(encoding="utf-8").splitlines()), 2)

    def test_open_persistence_failure_does_not_mutate_account(self):
        with tempfile.TemporaryDirectory() as directory:
            account, journal, session = self.make_session(directory)
            with patch.object(journal, "append", side_effect=JournalError("disk error")):
                with self.assertRaises(JournalError):
                    session.open_trade(
                        trade_id="paper-002", symbol="BTCIRT", strategy="test",
                        quantity=1, entry_price=100, initial_risk_quote=5,
                        entry_reason="test setup",
                    )
            self.assertEqual(account.cash, 1000)
            self.assertEqual(account.position, 0)
            self.assertEqual(account.fills, ())

    def test_close_persistence_failure_keeps_position_and_open_record(self):
        with tempfile.TemporaryDirectory() as directory:
            account, journal, session = self.make_session(directory)
            session.open_trade(
                trade_id="paper-003", symbol="BTCIRT", strategy="test",
                quantity=1, entry_price=100, initial_risk_quote=5,
                entry_reason="test setup",
            )
            cash_before = account.cash
            with patch.object(journal, "append", side_effect=JournalError("disk error")):
                with self.assertRaises(JournalError):
                    session.close_trade(exit_price=110, exit_reason="test exit")
            self.assertEqual(account.position, 1)
            self.assertEqual(account.cash, cash_before)
            self.assertEqual(journal.records()[0].status, "open")
            self.assertEqual(len(account.fills), 1)

    def test_only_one_open_trade_and_unique_trade_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            _, journal, session = self.make_session(directory)
            session.open_trade(
                trade_id="paper-004", symbol="BTCIRT", strategy="test",
                quantity=1, entry_price=100, initial_risk_quote=5,
                entry_reason="test setup",
            )
            with self.assertRaises(PaperSessionError):
                session.open_trade(
                    trade_id="paper-005", symbol="ETHIRT", strategy="test",
                    quantity=1, entry_price=10, initial_risk_quote=2,
                    entry_reason="second setup",
                )
            self.assertEqual(len(journal.records()), 1)

    def test_restart_restores_open_position_and_can_close_it(self):
        with tempfile.TemporaryDirectory() as directory:
            account, journal, session = self.make_session(directory)
            session.open_trade(
                trade_id="paper-006", symbol="BTCIRT", strategy="test",
                quantity=1, entry_price=100, initial_risk_quote=5,
                entry_reason="test setup",
            )
            restored = PaperTradeSession.restore(journal)
            self.assertAlmostEqual(restored.account.position, 1)
            self.assertAlmostEqual(restored.account.cash, account.cash)
            closed = restored.close_trade(exit_price=110, exit_reason="after restart")
            self.assertEqual(closed.status, "closed")
            self.assertEqual(restored.account.position, 0)

    def test_restart_restores_closed_account_cash_and_order_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            _, journal, session = self.make_session(directory)
            session.open_trade(
                trade_id="paper-006b", symbol="BTCIRT", strategy="test",
                quantity=1, entry_price=100, initial_risk_quote=5,
                entry_reason="test setup",
            )
            session.close_trade(exit_price=110, exit_reason="target")
            restored = PaperTradeSession.restore(journal)
            self.assertEqual(restored.account.position, 0)
            self.assertAlmostEqual(restored.account.cash, session.account.cash)
            self.assertEqual(len(restored.account.fills), 2)

    def test_restore_fails_closed_on_tampered_account_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            _, journal, session = self.make_session(directory)
            session.open_trade(
                trade_id="paper-006c", symbol="BTCIRT", strategy="test",
                quantity=1, entry_price=100, initial_risk_quote=5,
                entry_reason="test setup",
            )
            raw = journal.path.read_text(encoding="utf-8")
            raw = raw.replace('"account_cash_after":899.0', '"account_cash_after":1.0')
            journal.path.write_text(raw, encoding="utf-8")
            with self.assertRaisesRegex(PaperSessionError, "snapshot mismatch"):
                PaperTradeSession.restore(journal)

    def test_invalid_cost_is_rejected_before_account_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            account, _, session = self.make_session(directory)
            with self.assertRaises(PaperSessionError):
                session.open_trade(
                    trade_id="paper-007", symbol="BTCIRT", strategy="test",
                    quantity=1, entry_price=100, initial_risk_quote=5,
                    entry_reason="test setup", entry_slippage_quote=float("nan"),
                )
            self.assertEqual(account.position, 0)
            self.assertEqual(account.cash, 1000)


if __name__ == "__main__":
    unittest.main()
