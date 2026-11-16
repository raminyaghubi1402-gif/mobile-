import json
import tempfile
import unittest
from pathlib import Path

from lili_trader.paper_summary import render_paper_summary


class PaperSummaryTests(unittest.TestCase):
    def test_summary_reports_both_accounts_and_journal_payload_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            account = {
                "mode": "PAPER_ONLY_NO_EXCHANGE_ORDERS",
                "last_cycle_at": "2026-11-16T14:20:00+00:00",
                "usd_irt_rate": 2710000.0,
                "symbols": {
                    symbol: {
                        "last_market_data_at": "2026-11-16T14:20:00+00:00",
                        "new_decisions_last_cycle": 1,
                        "closed_trades_last_cycle": 0,
                        "open_trade_id": None,
                        "last_equity_irt": 13550000.0,
                        "last_marked_equity_irt": 13550000.0,
                    }
                    for symbol in ("BTCIRT", "ETHIRT")
                },
            }
            (root / "account.json").write_text(json.dumps(account), encoding="utf-8")
            events = [
                {"event_type": "DECISION", "payload": {"action": "WAIT"}},
                {"event_type": "DECISION", "payload": {"action": "BUY"}},
                {"event_type": "OUTCOME", "payload": {"net_pnl_quote": 100.0}},
                {"event_type": "OUTCOME", "payload": {"net_pnl_quote": -30.0}},
                {"event_type": "REVIEW", "payload": {"outcome": "BAD"}},
            ]
            (root / "decisions.jsonl").write_text(
                "".join(json.dumps(event) + "\n" for event in events),
                encoding="utf-8",
            )
            report = render_paper_summary(
                root, trigger_event="schedule", cycle_outcome="success"
            )
            self.assertIn("BTCIRT", report)
            self.assertIn("ETHIRT", report)
            self.assertIn("Journal decisions: **2**", report)
            self.assertIn("Journal outcomes: **2** (wins: 1, losses: 1)", report)
            self.assertIn("Net realized PnL (IRT): **70.00**", report)
            self.assertIn("Human reviews: **1**", report)
            self.assertIn('{"BUY": 1, "WAIT": 1}', report)
            self.assertIn("Trigger: `schedule`", report)

    def test_missing_state_and_journal_are_reported_without_inventing_data(self):
        with tempfile.TemporaryDirectory() as directory:
            report = render_paper_summary(directory, cycle_outcome="failure")
            self.assertIn("Account state is not present", report)
            self.assertIn("no decision file", report)
            self.assertIn("Cycle step: `failure`", report)


if __name__ == "__main__":
    unittest.main()
