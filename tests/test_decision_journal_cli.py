import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lili_trader.decision_journal import DecisionJournal
from lili_trader.__main__ import main


class DecisionJournalCliTests(unittest.TestCase):
    def test_cli_reports_decisions_and_review_tags(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "decisions.jsonl"
            journal = DecisionJournal(path)
            journal.append_decision(
                decision_id="cli-decision", occurred_at="2026-11-16T10:00:00+00:00",
                market_data_at="2026-11-16T09:55:00+00:00", symbol="ETHIRT",
                action="WAIT", strategy="trend-filter", strategy_version="0.1",
                reason="15m and 5m are not aligned", evidence={"15m": "mixed"},
            )
            journal.append_review(
                decision_id="cli-decision", occurred_at="2026-11-16T11:00:00+00:00",
                trade_id="paper-eth-1", outcome="NEUTRAL", failure_tags=["missed_move"],
                root_cause="filter was conservative", corrective_action="evaluate on OOS",
            )
            output = io.StringIO()
            with patch("sys.argv", ["lili-trader", "--decision-journal-report", str(path)]), contextlib.redirect_stdout(output):
                main()
            self.assertIn("WAIT=1", output.getvalue())
            self.assertIn("missed_move=1", output.getvalue())


if __name__ == "__main__":
    unittest.main()
