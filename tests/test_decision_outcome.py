import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from lili_trader.decision_journal import DecisionJournal
from lili_trader.decision_outcome import (
    DecisionOutcomeLinkError,
    append_backtest_outcomes,
)


class DecisionOutcomeLinkTests(unittest.TestCase):
    def make_fixture(self, directory, direction="BUY"):
        run_id = "run-123"
        symbol = "BTCIRT"
        entry_ts = 1_795_000_000
        decision_id = f"{run_id}:{symbol}:{entry_ts}"
        trade_id = decision_id
        journal = DecisionJournal(Path(directory) / "decisions.jsonl")
        journal.append_decision(
            decision_id=decision_id,
            occurred_at="2026-11-16T10:00:00+00:00",
            market_data_at="2026-11-16T09:55:00+00:00",
            symbol=symbol, action=direction,
            strategy="three_timeframe_price_action",
            strategy_version="0.2.0", reason="confirmed setup",
            evidence={"gate_status": "ready"},
            entry_price=100.0, stop_loss=95.0, take_profit=110.0,
            risk_quote=5.0, trade_id=trade_id,
        )
        trade = SimpleNamespace(
            direction=direction,
            entry_timestamp=entry_ts,
            exit_timestamp=entry_ts + 3600,
            entry_price=100.0, exit_price=108.0,
            quantity=1.0, gross_pnl=10.0, fees=2.0,
            slippage_quote=1.0, net_pnl=7.0,
            exit_reason="smart_stop",
        )
        result = SimpleNamespace(trades=(trade,))
        return journal, result, run_id, symbol

    def test_links_closed_trade_and_reconciles_all_costs(self):
        with tempfile.TemporaryDirectory() as directory:
            journal, result, run_id, symbol = self.make_fixture(directory)
            self.assertEqual(
                append_backtest_outcomes(result, journal, run_id=run_id, symbol=symbol), 1
            )
            outcome = [e for e in journal.events() if e.event_type == "OUTCOME"][0]
            self.assertEqual(outcome.decision_id, f"{run_id}:{symbol}:1795000000")
            self.assertEqual(outcome.payload["gross_pnl_quote"], 10.0)
            self.assertEqual(outcome.payload["fees_quote"], 2.0)
            self.assertEqual(outcome.payload["slippage_quote"], 1.0)
            self.assertEqual(outcome.payload["net_pnl_quote"], 7.0)
            self.assertEqual(outcome.payload["review_status"], "needs_human_review")
            self.assertEqual(journal.summary()["outcomes"], 1)

    def test_import_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            journal, result, run_id, symbol = self.make_fixture(directory)
            self.assertEqual(append_backtest_outcomes(result, journal, run_id=run_id, symbol=symbol), 1)
            self.assertEqual(append_backtest_outcomes(result, journal, run_id=run_id, symbol=symbol), 0)
            self.assertEqual(len([e for e in journal.events() if e.event_type == "OUTCOME"]), 1)

    def test_missing_decision_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = DecisionJournal(Path(directory) / "decisions.jsonl")
            trade = SimpleNamespace(
                direction="BUY", entry_timestamp=1795000000,
                exit_timestamp=1795003600, fees=2.0,
                slippage_quote=1.0, net_pnl=7.0, exit_reason="stop",
            )
            with self.assertRaises(DecisionOutcomeLinkError):
                append_backtest_outcomes(
                    SimpleNamespace(trades=(trade,)), journal,
                    run_id="missing", symbol="BTCIRT",
                )

    def test_direction_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            journal, result, run_id, symbol = self.make_fixture(directory, direction="SELL")
            result.trades[0].direction = "BUY"
            with self.assertRaises(DecisionOutcomeLinkError):
                append_backtest_outcomes(result, journal, run_id=run_id, symbol=symbol)


if __name__ == "__main__":
    unittest.main()
