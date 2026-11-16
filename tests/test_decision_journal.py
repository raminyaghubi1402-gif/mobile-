import json
import tempfile
import unittest
from pathlib import Path

from lili_trader.decision_journal import DecisionJournal, DecisionJournalError


class DecisionJournalTests(unittest.TestCase):
    def decision(self, journal, action="BUY"):
        return journal.append_decision(
            decision_id="d-001", occurred_at="2026-11-16T10:05:00+00:00",
            market_data_at="2026-11-16T10:00:00+00:00",
            symbol="BTCIRT", action=action, strategy="trend-pullback",
            strategy_version="0.1.0", reason="1h bullish; 15m pullback; 5m trigger",
            evidence={"1h": "HH_HL", "15m": "pullback_end", "5m": "bullish_close"},
            entry_price=100 if action != "WAIT" else None,
            stop_loss=95 if action != "WAIT" else None,
            take_profit=110 if action != "WAIT" else None,
            risk_quote=2 if action != "WAIT" else None,
        )

    def test_decision_and_review_are_append_only_and_summarized(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = DecisionJournal(Path(directory) / "decisions.jsonl")
            self.decision(journal)
            journal.append_review(
                decision_id="d-001", occurred_at="2026-11-16T11:00:00+00:00",
                trade_id="paper-001", outcome="BAD",
                failure_tags=["late_entry", "weak_confirmation"],
                root_cause="5m trigger was not strong enough",
                corrective_action="require a closed-candle retest",
            )
            summary = journal.summary()
            self.assertEqual(summary["decisions"], 1)
            self.assertEqual(summary["buy"], 1)
            self.assertEqual(summary["reviews"], 1)
            self.assertEqual(summary["bad_reviews"], 1)
            self.assertEqual(summary["failure_tags"]["late_entry"], 1)
            self.assertEqual(len(journal.events()), 2)

    def test_wait_decision_does_not_require_trade_levels(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = DecisionJournal(Path(directory) / "decisions.jsonl")
            self.decision(journal, action="WAIT")
            self.assertEqual(journal.summary()["wait"], 1)

    def test_rejects_out_of_scope_symbol_and_duplicate_review(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = DecisionJournal(Path(directory) / "decisions.jsonl")
            with self.assertRaises(DecisionJournalError):
                journal.append_decision(
                    decision_id="bad", occurred_at="2026-11-16T10:05:00+00:00",
                    market_data_at="2026-11-16T10:00:00+00:00",
                    symbol="USDTIRT", action="WAIT", strategy="x",
                    strategy_version="1", reason="test", evidence={},
                )
            self.decision(journal)
            args = dict(
                decision_id="d-001", occurred_at="2026-11-16T11:00:00+00:00",
                trade_id="paper-001", outcome="GOOD", failure_tags=[],
                root_cause="rules followed", corrective_action="no change",
            )
            journal.append_review(**args)
            with self.assertRaises(DecisionJournalError):
                journal.append_review(**args)

    def test_hash_chain_detects_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "decisions.jsonl"
            journal = DecisionJournal(path)
            self.decision(journal)
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["payload"]["reason"] = "edited after the fact"
            path.write_text(json.dumps(raw) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(DecisionJournalError, "hash mismatch"):
                journal.events()

    def test_review_cannot_precede_decision(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = DecisionJournal(Path(directory) / "decisions.jsonl")
            with self.assertRaises(DecisionJournalError):
                journal.append_review(
                    decision_id="missing", occurred_at="2026-11-16T11:00:00+00:00",
                    trade_id="paper-001", outcome="BAD", failure_tags=["bad_signal"],
                    root_cause="unknown", corrective_action="investigate",
                )


if __name__ == "__main__":
    unittest.main()
