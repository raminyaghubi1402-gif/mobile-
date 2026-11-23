import csv
import tempfile
import unittest
from pathlib import Path

from lili_trader.history_gap_recovery import recover_missing_5m_candles
from lili_trader.nobitex_data import NobitexCandle


def candle(timestamp, close=100.0):
    return NobitexCandle(
        symbol="BTCIRT", resolution="5", timestamp=timestamp,
        open=close, high=close + 1, low=close - 1, close=close,
        volume=1.0, fetched_at=2_000,
    )


def write_history(path, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("timestamp", "open", "high", "low", "close", "volume"))
        writer.writerows(rows)


def read_timestamps(path):
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [int(row["timestamp"]) for row in csv.DictReader(handle)]


class FakeAdapter:
    def __init__(self, returned=None, error=None):
        self.returned = list(returned or [])
        self.error = error
        self.calls = []

    def fetch_recent(self, symbol, resolution, count, *, page, to_timestamp):
        self.calls.append((symbol, resolution, count, page, to_timestamp))
        if self.error:
            raise self.error
        return list(self.returned)


class HistoryGapRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "BTCIRT_5.csv"
        write_history(self.path, [
            [300, 100, 101, 99, 100, 1],
            [600, 100, 101, 99, 100, 1],
            [1200, 200, 201, 199, 200, 1],
        ])

    def tearDown(self):
        self.temp.cleanup()

    def test_inserts_only_api_confirmed_missing_timestamp_and_preserves_existing(self):
        adapter = FakeAdapter([candle(900, 150), candle(1200, 999)])
        result = recover_missing_5m_candles(self.path, "BTCIRT", adapter)

        self.assertEqual(result["status"], "clean")
        self.assertEqual(result["recovered_timestamps"], [900])
        self.assertEqual(read_timestamps(self.path), [300, 600, 900, 1200])
        self.assertEqual(adapter.calls, [("BTCIRT", "5", 500, 1, 1500)])
        with self.path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(float(rows[-1]["close"]), 200.0)

    def test_no_api_candle_means_gap_remains_and_nothing_is_synthesized(self):
        original = self.path.read_text(encoding="utf-8")
        adapter = FakeAdapter([candle(600), candle(1200)])
        result = recover_missing_5m_candles(self.path, "BTCIRT", adapter)

        self.assertEqual(result["status"], "unresolved")
        self.assertEqual(result["missing_candles_before"], 1)
        self.assertEqual(result["missing_candles_after"], 1)
        self.assertEqual(result["recovered_timestamps"], [])
        self.assertEqual(self.path.read_text(encoding="utf-8"), original)

    def test_irregular_or_nonchronological_history_is_not_queried(self):
        for rows, expected in (
            ([[300, 100, 101, 99, 100, 1], [601, 100, 101, 99, 100, 1]], "irregular_interval"),
            ([[1200, 100, 101, 99, 100, 1], [600, 100, 101, 99, 100, 1]], "non_chronological"),
        ):
            with self.subTest(expected=expected):
                write_history(self.path, rows)
                adapter = FakeAdapter([candle(900)])
                result = recover_missing_5m_candles(self.path, "BTCIRT", adapter)
                self.assertEqual(result["status"], expected)
                self.assertEqual(adapter.calls, [])

    def test_gap_larger_than_api_limit_is_skipped_without_request(self):
        next_timestamp = 300 + 502 * 300  # 501 missing bars
        write_history(self.path, [
            [300, 100, 101, 99, 100, 1],
            [next_timestamp, 100, 101, 99, 100, 1],
        ])
        adapter = FakeAdapter([candle(600)])
        result = recover_missing_5m_candles(self.path, "BTCIRT", adapter)

        self.assertEqual(result["status"], "unresolved")
        self.assertEqual(result["skipped_intervals"], 1)
        self.assertEqual(adapter.calls, [])

    def test_api_errors_are_reported_by_type_without_exposing_details(self):
        adapter = FakeAdapter(error=RuntimeError("secret-like response detail"))
        result = recover_missing_5m_candles(self.path, "BTCIRT", adapter)

        self.assertEqual(result["status"], "unresolved")
        self.assertEqual(result["errors"], ["RuntimeError"])
        self.assertNotIn("secret-like", str(result))

    def test_recovery_is_limited_to_supported_symbols(self):
        with self.assertRaises(ValueError):
            recover_missing_5m_candles(self.path, "OTHER", FakeAdapter())

    def test_max_gap_query_count_is_validated(self):
        for value in (0, -1, True, 1.5):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    recover_missing_5m_candles(self.path, "BTCIRT", FakeAdapter(), max_gap_queries=value)


if __name__ == "__main__":
    unittest.main()
