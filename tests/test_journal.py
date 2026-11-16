import tempfile
import unittest
from pathlib import Path

from lili_trader.journal import JournalError, TradeJournal, TradeRecord


def closed_trade(**overrides):
    values = dict(
        trade_id="paper-001", symbol="BTCIRT", strategy="bos_pullback",
        side="long", status="closed", entry_time="2026-11-16T10:00:00+00:00",
        entry_price=100.0, quantity=0.1, initial_risk_quote=2.0,
        entry_reason="1h bullish BOS; 15m pullback; 5m close confirmation",
        exit_time="2026-11-16T11:00:00+00:00", exit_price=104.0,
        gross_pnl_quote=0.4, fees_quote=0.02, slippage_quote=0.01,
        net_pnl_quote=0.37, exit_reason="take_profit",
    )
    values.update(overrides)
    return TradeRecord(**values)


class TradeJournalTests(unittest.TestCase):
    def test_append_and_read_closed_trade_with_net_r(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = TradeJournal(Path(directory) / "journal" / "trades.jsonl")
            journal.append(closed_trade())
            loaded = journal.records()
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].trade_id, "paper-001")
            self.assertAlmostEqual(loaded[0].r_multiple, 0.185)

    def test_open_trade_has_no_exit_or_pnl_fields(self):
        trade = TradeRecord(
            trade_id="paper-open", symbol="ETHIRT", strategy="trend",
            side="short", status="open", entry_time="2026-11-16T10:00:00Z",
            entry_price=3000, quantity=0.01, initial_risk_quote=3,
            entry_reason="1h bearish structure",
        )
        with tempfile.TemporaryDirectory() as directory:
            journal = TradeJournal(Path(directory) / "trades.jsonl")
            journal.append(trade)
            self.assertIsNone(journal.records()[0].r_multiple)

    def test_duplicate_trade_id_is_rejected_without_changing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trades.jsonl"
            journal = TradeJournal(path)
            journal.append(closed_trade())
            original = path.read_bytes()
            with self.assertRaises(JournalError):
                journal.append(closed_trade())
            self.assertEqual(path.read_bytes(), original)

    def test_net_pnl_must_reconcile_fees_and_slippage(self):
        with self.assertRaisesRegex(JournalError, "net PnL"):
            closed_trade(net_pnl_quote=0.4).validate()

    def test_timezone_is_required(self):
        with self.assertRaises(JournalError):
            closed_trade(entry_time="2026-11-16T10:00:00").validate()

    def test_exit_cannot_precede_entry(self):
        with self.assertRaises(JournalError):
            closed_trade(exit_time="2026-11-16T09:00:00+00:00").validate()

    def test_nan_and_boolean_financial_values_are_rejected(self):
        for value in (float("nan"), float("inf"), True, "100"):
            with self.subTest(value=value):
                with self.assertRaises(JournalError):
                    closed_trade(entry_price=value).validate()

    def test_open_record_cannot_smuggle_exit_values(self):
        with self.assertRaises(JournalError):
            TradeRecord(
                trade_id="open", symbol="BTCIRT", strategy="test", side="long",
                status="open", entry_time="2026-11-16T10:00:00Z",
                entry_price=100, quantity=1, initial_risk_quote=2,
                entry_reason="test", exit_price=110,
            ).validate()


    def test_open_trade_can_receive_one_matching_close_event(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = TradeJournal(Path(directory) / "trades.jsonl")
            opened = TradeRecord(
                trade_id="lifecycle-1", symbol="BTCIRT", strategy="bos_pullback",
                side="long", status="open", entry_time="2026-11-16T10:00:00Z",
                entry_price=100, quantity=0.1, initial_risk_quote=2,
                entry_reason="1h bullish BOS; 15m pullback; 5m close confirmation",
            )
            closed = closed_trade(
                trade_id="lifecycle-1", entry_time="2026-11-16T10:00:00Z",
                exit_time="2026-11-16T11:00:00Z",
            )
            journal.append(opened)
            journal.append(closed)
            records = journal.records()
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].status, "closed")
            self.assertAlmostEqual(records[0].r_multiple, 0.185)
            self.assertEqual(len(journal.path.read_text(encoding="utf-8").splitlines()), 2)

    def test_closed_trade_cannot_be_closed_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = TradeJournal(Path(directory) / "trades.jsonl")
            journal.append(closed_trade())
            with self.assertRaises(JournalError):
                journal.append(closed_trade(exit_price=105, gross_pnl_quote=0.5,
                                            net_pnl_quote=0.47))

    def test_close_event_cannot_change_trade_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = TradeJournal(Path(directory) / "trades.jsonl")
            opened = TradeRecord(
                trade_id="identity-1", symbol="BTCIRT", strategy="trend",
                side="long", status="open", entry_time="2026-11-16T10:00:00Z",
                entry_price=100, quantity=0.1, initial_risk_quote=2,
                entry_reason="entry",
            )
            journal.append(opened)
            with self.assertRaisesRegex(JournalError, "identity"):
                journal.append(closed_trade(
                    trade_id="identity-1", strategy="different",
                    entry_time="2026-11-16T10:00:00Z",
                ))

    def test_corrupt_journal_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trades.jsonl"
            path.write_text("{broken json}\n", encoding="utf-8")
            with self.assertRaisesRegex(JournalError, "corruption"):
                TradeJournal(path).records()


if __name__ == "__main__":
    unittest.main()
