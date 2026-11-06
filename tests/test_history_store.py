import tempfile
import unittest
from pathlib import Path

from lili_trader.history_store import (
    load_history_csv,
    merge_candle_history,
    merge_history_csv,
)
from lili_trader.nobitex_data import NobitexCandle


def candle(ts, close, resolution="5"):
    return NobitexCandle(
        symbol="BTCIRT", resolution=resolution, timestamp=ts,
        open=close, high=close + 1, low=close - 1, close=close,
        volume=1.0, fetched_at=100,
    )


class HistoryStoreTests(unittest.TestCase):
    def test_merge_deduplicates_prefers_incoming_and_sorts(self):
        merged = merge_candle_history(
            [candle(300, 101), candle(0, 100)],
            [candle(300, 102), candle(600, 103)],
        )
        self.assertEqual([row.timestamp for row in merged], [0, 300, 600])
        self.assertEqual(merged[1].close, 102)

    def test_merge_applies_history_limit(self):
        merged = merge_candle_history(
            [], [candle(0, 100), candle(300, 101), candle(600, 102)],
            max_rows=2,
        )
        self.assertEqual([row.timestamp for row in merged], [300, 600])

    def test_csv_round_trip_and_deduplication(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history" / "BTCIRT_5.csv"
            first = merge_history_csv(path, [candle(0, 100), candle(300, 101)])
            second = merge_history_csv(path, [candle(300, 102), candle(600, 103)])
            loaded = load_history_csv(path, symbol="BTCIRT", resolution="5")
            self.assertEqual(len(first), 2)
            self.assertEqual(len(second), 3)
            self.assertEqual([row.timestamp for row in loaded], [0, 300, 600])
            self.assertEqual(loaded[1].close, 102)

    def test_invalid_ohlc_is_rejected(self):
        invalid = NobitexCandle(
            symbol="BTCIRT", resolution="5", timestamp=0,
            open=100, high=99, low=98, close=100, volume=1,
        )
        with self.assertRaises(ValueError):
            merge_candle_history([], [invalid])


if __name__ == "__main__":
    unittest.main()
