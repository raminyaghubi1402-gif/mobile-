import io
import sys
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

from lili_trader.__main__ import main
from lili_trader.paper_forward import ForwardPaperError


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

    def test_unexpected_exception_text_is_not_exposed(self):
        output = self._run_cli_with_error(
            RuntimeError("authorization=must-not-appear")
        )
        self.assertIn("RuntimeError", output)
        self.assertNotIn("authorization=must-not-appear", output)


if __name__ == "__main__":
    unittest.main()
