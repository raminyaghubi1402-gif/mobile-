import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lili_trader.journal import TradeJournal, TradeRecord
from lili_trader.__main__ import main


class JournalReportCliTests(unittest.TestCase):
    def test_cli_prints_persian_report_from_jsonl(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trades.jsonl"
            journal = TradeJournal(path)
            journal.append(TradeRecord(
                trade_id="cli-1", symbol="BTCIRT", strategy="test", side="long",
                status="closed", entry_time="2026-11-16T09:00:00+00:00",
                entry_price=100, quantity=1, initial_risk_quote=5,
                entry_reason="test", exit_time="2026-11-16T10:00:00+00:00",
                exit_price=110, gross_pnl_quote=10, fees_quote=1,
                slippage_quote=1, net_pnl_quote=8, exit_reason="test exit",
            ))
            output = io.StringIO()
            with patch("sys.argv", [
                "lili-trader", "--journal-report", str(path),
                "--journal-starting-equity", "100",
            ]), contextlib.redirect_stdout(output):
                main()
            self.assertIn("گزارش عملکرد ژورنال", output.getvalue())
            self.assertIn("سود خالص: 8.00", output.getvalue())


if __name__ == "__main__":
    unittest.main()
