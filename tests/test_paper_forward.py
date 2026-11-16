import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone

from lili_trader.nobitex_data import NobitexCandle
from lili_trader.paper_forward import (
    ForwardPaperError,
    _initial_state,
    _validate_seed_rate,
    run_forward_cycle,
)


class FakeAdapter:
    def __init__(self, now_ts):
        self.now_ts = now_ts

    def fetch_history(self, symbol, resolution, count):
        if resolution == "5":
            step, rows = 300, 120
        elif resolution == "15":
            step, rows = 900, 40
        else:
            step, rows = 3600, 40
        end = self.now_ts - self.now_ts % step
        start = end - (rows - 1) * step
        result = []
        for index in range(rows):
            timestamp = start + index * step
            price = 100.0 + index * 0.1
            result.append(NobitexCandle(
                symbol=symbol, resolution=resolution, timestamp=timestamp,
                open=price, high=price + 1, low=price - 1,
                close=price + 0.5, volume=10.0, fetched_at=self.now_ts,
            ))
        return result


class ForwardPaperTests(unittest.TestCase):
    def test_seed_rate_requires_fresh_timezone_aware_provenance(self):
        now = 1_795_000_000
        observed = datetime.fromtimestamp(now - 60, timezone.utc).isoformat()
        rate, source, at = _validate_seed_rate(1_000_000, "user supplied market quote", observed, now)
        self.assertEqual(rate, 1_000_000)
        self.assertEqual(source, "user supplied market quote")
        self.assertTrue(at.endswith("+00:00"))
        with self.assertRaises(ForwardPaperError):
            _validate_seed_rate(1_000_000, "source", datetime.fromtimestamp(now - 901, timezone.utc).isoformat(), now)
        with self.assertRaises(ForwardPaperError):
            _validate_seed_rate(True, "source", observed, now)
        with self.assertRaises(ForwardPaperError):
            _validate_seed_rate(1_000_000, "source", "2026-11-16T10:00:00", now)

    def test_initial_state_splits_ten_dollars_between_two_spot_pairs(self):
        now = 1_795_000_000
        state = _initial_state(
            1_000_000, "source", datetime.fromtimestamp(now, timezone.utc).isoformat(),
            {"BTCIRT": 1_794_999_900, "ETHIRT": 1_794_999_900}, now,
        )
        self.assertEqual(state["initial_capital_usd"], 10.0)
        self.assertEqual(state["capital_allocation"], "5 USD per symbol; total 10 USD")
        self.assertEqual(state["symbols"]["BTCIRT"]["starting_equity_irt"], 5_000_000)
        self.assertEqual(state["symbols"]["ETHIRT"]["starting_equity_irt"], 5_000_000)
        self.assertEqual(state["mode"], "PAPER_ONLY_NO_EXCHANGE_ORDERS")

    def test_first_cycle_initializes_without_replaying_historical_signals(self):
        now = 1_795_000_000
        rate_at = datetime.fromtimestamp(now - 30, timezone.utc).isoformat()
        with tempfile.TemporaryDirectory() as directory:
            result = run_forward_cycle(
                directory, usd_irt_rate=1_000_000,
                usd_irt_rate_source="manual verified quote",
                usd_irt_rate_at=rate_at,
                adapter=FakeAdapter(now), now_ts=now,
            )
            self.assertEqual(result["status"], "initialized")
            self.assertEqual(result["initial_capital_usd"], 10.0)
            self.assertTrue((Path(directory) / "account.json").exists())
            self.assertFalse((Path(directory) / "decisions.jsonl").exists())

    def test_uninitialized_cycle_waits_for_explicit_seed_rate(self):
        now = 1_795_000_000
        with tempfile.TemporaryDirectory() as directory:
            result = run_forward_cycle(
                directory, adapter=FakeAdapter(now), now_ts=now,
            )
            self.assertEqual(result["status"], "awaiting_initialization")
            self.assertFalse((Path(directory) / "account.json").exists())

    def test_stale_current_five_minute_candle_fails_closed(self):
        now = 1_795_000_000
        adapter = FakeAdapter(now - 300)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ForwardPaperError):
                run_forward_cycle(
                    directory, usd_irt_rate=1_000_000,
                    usd_irt_rate_source="source",
                    usd_irt_rate_at=datetime.fromtimestamp(now, timezone.utc).isoformat(),
                    adapter=adapter, now_ts=now,
                )


if __name__ == "__main__":
    unittest.main()
