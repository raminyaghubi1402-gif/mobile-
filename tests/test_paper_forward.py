import unittest
from datetime import datetime, timezone

from lili_trader.paper_forward import (
    ForwardPaperError,
    _validate_seed_rate,
    run_forward_cycle,
)


class NoFetchAdapter:
    def __init__(self):
        self.calls = 0

    def fetch_history(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("disabled legacy engine must not fetch market data")


class ForwardPaperTests(unittest.TestCase):
    def test_seed_rate_validation_remains_available_for_future_paper_engine(self):
        now = 1_795_000_000
        observed = datetime.fromtimestamp(now - 60, timezone.utc).isoformat()
        rate, source, at = _validate_seed_rate(
            1_000_000, "user supplied market quote", observed, now
        )
        self.assertEqual(rate, 1_000_000)
        self.assertEqual(source, "user supplied market quote")
        self.assertTrue(at.endswith("+00:00"))

    def test_seed_rate_rejects_stale_boolean_and_timezone_naive_values(self):
        now = 1_795_000_000
        with self.assertRaises(ForwardPaperError):
            _validate_seed_rate(
                1_000_000, "source",
                datetime.fromtimestamp(now - 901, timezone.utc).isoformat(), now
            )
        with self.assertRaises(ForwardPaperError):
            _validate_seed_rate(
                True, "source",
                datetime.fromtimestamp(now - 60, timezone.utc).isoformat(), now
            )
        with self.assertRaises(ForwardPaperError):
            _validate_seed_rate(1_000_000, "source", "2026-11-16T10:00:00", now)

    def test_legacy_cycle_is_disabled_before_network_or_state_access(self):
        adapter = NoFetchAdapter()
        result = run_forward_cycle(
            "/this/path/must/not/be/touched",
            usd_irt_rate=1_000_000,
            usd_irt_rate_source="ignored",
            usd_irt_rate_at="2026-11-18T00:00:00+00:00",
            adapter=adapter,
            now_ts=1_795_000_000,
        )
        self.assertEqual(result["status"], "disabled_legacy_engine")
        self.assertEqual(result["symbols"], {})
        self.assertEqual(result["journal_summary"]["decisions"], 0)
        self.assertEqual(adapter.calls, 0)


if __name__ == "__main__":
    unittest.main()
