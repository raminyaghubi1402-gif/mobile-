import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from lili_trader.__main__ import main
from lili_trader.paper_forward import ForwardPaperError, StaleMarketDataError


class PaperForwardCliTests(unittest.TestCase):
    def _run_cli_with_error(self, error):
        stderr = io.StringIO()
        with patch.object(sys, "argv", ["lili-trader", "--paper-forward"]):
            with patch(
                "lili_trader.paper_forward.run_forward_cycle",
                side_effect=error,
            ):
                with redirect_stderr(stderr):
                    with self.assertRaises(SystemExit) as raised:
                        main()
        self.assertEqual(raised.exception.code, 2)
        return stderr.getvalue()

    def test_forward_validation_reason_is_visible(self):
        message = "BTCIRT current 5m candle is stale (300s)"
        output = self._run_cli_with_error(ForwardPaperError(message))
        self.assertIn(message, output)
        self.assertIn("هیچ سفارش واقعی ارسال نشده است", output)

    def test_stale_market_data_is_a_safe_skip_and_is_reported_to_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "github-output"
            stdout = io.StringIO()
            with patch.object(sys, "argv", ["lili-trader", "--paper-forward"]):
                with patch(
                    "lili_trader.paper_forward.run_forward_cycle",
                    side_effect=StaleMarketDataError("BTCIRT current 5m candle is stale (270s)"),
                ):
                    with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output_path)}):
                        with redirect_stdout(stdout):
                            main()
            self.assertIn("دادهٔ کندل ۵ دقیقه‌ای به‌روز نیست", stdout.getvalue())
            self.assertIn("status=skipped_stale_data", output_path.read_text(encoding="utf-8"))

    def test_unexpected_exception_text_is_not_exposed(self):
        output = self._run_cli_with_error(
            RuntimeError("authorization=must-not-appear")
        )
        self.assertIn("RuntimeError", output)
        self.assertNotIn("authorization=must-not-appear", output)


if __name__ == "__main__":
    unittest.main()
