import csv
import tempfile
import unittest
from pathlib import Path

from scripts.audit_history_gaps import audit_csv


FIELDS = ["timestamp", "open", "high", "low", "close", "volume"]


def write_rows(path: Path, timestamps):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for timestamp in timestamps:
            writer.writerow({
                "timestamp": timestamp, "open": 100, "high": 101,
                "low": 99, "close": 100, "volume": 1,
            })


class HistoryGapAuditTests(unittest.TestCase):
    def test_contiguous_history_passes_without_assuming_epoch_alignment(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "BTCIRT_5.csv"
            write_rows(path, [301, 601, 901])
            report = audit_csv(path, "5")
            self.assertTrue(report["clean"])
            self.assertEqual(report["row_count"], 3)
            self.assertEqual(report["missing_candle_count"], 0)

    def test_gap_reports_exact_missing_candle_count_and_timestamps(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "BTCIRT_5.csv"
            write_rows(path, [300, 600, 1200, 1500])
            report = audit_csv(path, "5")
            self.assertFalse(report["clean"])
            self.assertEqual(report["gap_count"], 1)
            self.assertEqual(report["missing_candle_count"], 1)
            gap = report["issues"][0]
            self.assertEqual(gap["previous_timestamp"], 600)
            self.assertEqual(gap["timestamp"], 1200)
            self.assertEqual(gap["missing_candles"], 1)

    def test_irregular_interval_is_not_misreported_as_a_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ETHIRT_5.csv"
            write_rows(path, [300, 600, 950])
            report = audit_csv(path, "5")
            self.assertEqual(report["issues"][0]["type"], "irregular_interval")
            self.assertEqual(report["missing_candle_count"], 0)

    def test_duplicate_and_out_of_order_timestamps_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "BTCIRT_15.csv"
            write_rows(path, [900, 900, 1800, 1500])
            report = audit_csv(path, "15")
            self.assertEqual([issue["type"] for issue in report["issues"]],
                             ["duplicate", "out_of_order"])

    def test_missing_file_and_invalid_header_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = audit_csv(Path(tmp) / "absent.csv", "5")
            self.assertEqual(missing["issues"][0]["type"], "missing_file")
            wrong = Path(tmp) / "wrong.csv"
            wrong.write_text("time,price\n1,100\n", encoding="utf-8")
            invalid = audit_csv(wrong, "5")
            self.assertEqual(invalid["issues"][0]["type"], "invalid_header")

    def test_invalid_timestamp_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "BTCIRT_5.csv"
            path.write_text(
                "timestamp,open,high,low,close,volume\n"
                "300,100,101,99,100,1\n"
                "bad,100,101,99,100,1\n",
                encoding="utf-8",
            )
            report = audit_csv(path, "5")
            self.assertEqual(report["issues"][0]["type"], "invalid_timestamp")
            self.assertFalse(report["clean"])


if __name__ == "__main__":
    unittest.main()
