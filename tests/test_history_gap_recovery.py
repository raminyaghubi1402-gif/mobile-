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
    def __init__(self, returned=None, error=None, returned_by_resolution=None):
        self.returned = list(returned or [])
        self.returned_by_resolution = returned_by_resolution or {}
        self.error = error
        self.calls = []

    def fetch_recent(self, symbol, resolution, count, *, page, to_timestamp, from_timestamp=None):
        self.calls.append((symbol, resolution, count, page, from_timestamp, to_timestamp))
        if self.error:
            raise self.error
        return list(self.returned_by_resolution.get(resolution, self.returned))


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
        self.assertEqual(adapter.calls, [("BTCIRT", "5", 500, 1, 900, 1199)])
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


    def test_reconstructs_only_from_five_exact_nobitex_one_minute_candles(self):
        minute_rows = [
            NobitexCandle(
                symbol="BTCIRT", resolution="1", timestamp=900 + offset * 60,
                open=100 + offset, high=105 + offset, low=99 + offset,
                close=104 + offset, volume=offset + 1, fetched_at=2_000,
            )
            for offset in range(5)
        ]
        adapter = FakeAdapter(returned_by_resolution={"5": [], "1": minute_rows})
        result = recover_missing_5m_candles(self.path, "BTCIRT", adapter)

        self.assertEqual(result["status"], "clean")
        self.assertEqual(result["recovered_timestamps"], [900])
        self.assertEqual(result["recovered_from_1m"], [900])
        self.assertEqual(read_timestamps(self.path), [300, 600, 900, 1200])
        with self.path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        reconstructed = rows[2]
        self.assertEqual(float(reconstructed["open"]), 100.0)
        self.assertEqual(float(reconstructed["high"]), 109.0)
        self.assertEqual(float(reconstructed["low"]), 99.0)
        self.assertEqual(float(reconstructed["close"]), 108.0)
        self.assertEqual(float(reconstructed["volume"]), 15.0)
        manifest = self.path.with_name("BTCIRT_5_recovery_manifest.jsonl")
        record = __import__("json").loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(record["method"], "aggregate_exact_1m")
        self.assertEqual(record["source_timestamps"], [900, 960, 1020, 1080, 1140])

    def test_incomplete_one_minute_data_never_creates_a_five_minute_candle(self):
        minute_rows = [
            NobitexCandle(
                symbol="BTCIRT", resolution="1", timestamp=900 + offset * 60,
                open=100, high=101, low=99, close=100, volume=1, fetched_at=2_000,
            )
            for offset in (0, 1, 3, 4)
        ]
        adapter = FakeAdapter(returned_by_resolution={"5": [], "1": minute_rows})
        original = self.path.read_text(encoding="utf-8")
        result = recover_missing_5m_candles(self.path, "BTCIRT", adapter)

        self.assertEqual(result["status"], "unresolved")
        self.assertEqual(result["recovered_timestamps"], [])
        self.assertEqual(result["recovered_from_1m"], [])
        self.assertEqual(result["missing_candles_after"], 1)
        self.assertEqual(self.path.read_text(encoding="utf-8"), original)
        self.assertFalse(self.path.with_name("BTCIRT_5_recovery_manifest.jsonl").exists())


    def test_duplicate_one_minute_rows_are_rejected(self):
        minute_rows = [
            NobitexCandle(
                symbol="BTCIRT", resolution="1", timestamp=900 + offset * 60,
                open=100, high=101, low=99, close=100, volume=1, fetched_at=2_000,
            )
            for offset in range(5)
        ]
        minute_rows.append(minute_rows[-1])
        adapter = FakeAdapter(returned_by_resolution={"5": [], "1": minute_rows})
        original = self.path.read_text(encoding="utf-8")
        result = recover_missing_5m_candles(self.path, "BTCIRT", adapter)

        self.assertEqual(result["status"], "unresolved")
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

    def test_missing_file_and_clean_history_do_not_make_network_requests(self):
        adapter = FakeAdapter()
        missing = recover_missing_5m_candles(self.path.with_name("missing.csv"), "BTCIRT", adapter)
        self.assertEqual(missing["status"], "missing_history")
        self.assertEqual(adapter.calls, [])

        write_history(self.path, [
            [300, 100, 101, 99, 100, 1],
            [600, 100, 101, 99, 100, 1],
            [900, 100, 101, 99, 100, 1],
        ])
        clean = recover_missing_5m_candles(self.path, "BTCIRT", adapter)
        self.assertEqual(clean["status"], "clean")
        self.assertEqual(adapter.calls, [])

    def test_api_errors_are_reported_by_type_without_exposing_details(self):
        adapter = FakeAdapter(error=RuntimeError("secret-like response detail"))
        result = recover_missing_5m_candles(self.path, "BTCIRT", adapter)

        self.assertEqual(result["status"], "unresolved")
        self.assertEqual(result["errors"], ["RuntimeError", "RuntimeError"])
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
