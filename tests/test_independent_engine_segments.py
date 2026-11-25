import unittest
from types import SimpleNamespace

from lili_trader.__main__ import (
    _build_contiguous_engine_backtest_windows,
    _contiguous_segments,
)


def candles(start, count, step):
    return [SimpleNamespace(timestamp=start + index * step) for index in range(count)]


class IndependentEngineSegmentTests(unittest.TestCase):
    def test_splits_every_timeframe_without_bridging_missing_candles(self):
        rows = candles(0, 4, 300) + candles(1800, 3, 300)
        segments = _contiguous_segments(rows, 300)
        self.assertEqual(
            [[row.timestamp for row in segment] for segment in segments],
            [[0, 300, 600, 900], [1800, 2100, 2400]],
        )

    def test_builds_two_independent_windows_from_two_five_minute_segments(self):
        base = 1_800_000_000
        hourly = candles(base - 40 * 3600, 60, 3600)
        fifteen = candles(base - 40 * 3600, 240, 900)
        five = candles(base, 30, 300) + candles(base + 9300, 30, 300)
        windows = _build_contiguous_engine_backtest_windows(hourly, fifteen, five)
        self.assertEqual(len(windows), 2)
        self.assertEqual(windows[0][2][0].timestamp, base)
        self.assertEqual(windows[0][2][-1].timestamp, base + 29 * 300)
        self.assertEqual(windows[1][2][0].timestamp, base + 9300)
        self.assertEqual(windows[1][2][-1].timestamp, base + 9300 + 29 * 300)

    def test_higher_timeframe_gap_splits_the_valid_five_minute_windows(self):
        base = 1_800_000_000
        hourly = (
            candles(base - 40 * 3600, 45, 3600)
            + candles(base + 10 * 3600, 45, 3600)
        )
        fifteen = (
            candles(base - 40 * 3600, 180, 900)
            + candles(base + 42 * 3600 + 1800, 210, 900)
        )
        five = (
            candles(base, 60, 300)
            + candles(base + 50 * 3600, 60, 300)
        )
        windows = _build_contiguous_engine_backtest_windows(hourly, fifteen, five)
        self.assertEqual(len(windows), 2)
        self.assertEqual(windows[0][2][0].timestamp, base)
        self.assertEqual(windows[1][2][0].timestamp, base + 50 * 3600)

    def test_allows_higher_timeframe_warmup_to_accumulate_after_segment_start(self):
        base = 1_800_000_000
        windows = _build_contiguous_engine_backtest_windows(
            candles(base - 40 * 3600, 50, 3600),
            candles(base, 60, 900),
            candles(base, 180, 300),
        )
        self.assertEqual(len(windows), 1)
        # The first 15m bar is not closed at the first 5m decision, but
        # sufficient 15m history exists later in this same valid overlap.
        self.assertEqual(len(windows[0][2]), 120)

    def test_rejects_segments_without_higher_timeframe_warmup(self):
        base = 1_800_000_000
        windows = _build_contiguous_engine_backtest_windows(
            candles(base, 29, 3600),
            candles(base, 29, 900),
            candles(base, 60, 300),
        )
        self.assertEqual(windows, [])

    def test_rejects_duplicate_timestamps_in_history(self):
        rows = candles(0, 3, 300)
        rows.insert(2, SimpleNamespace(timestamp=300))
        with self.assertRaises(ValueError):
            _contiguous_segments(rows, 300)

    def test_rejects_out_of_order_timestamps_in_history(self):
        rows = [
            SimpleNamespace(timestamp=0),
            SimpleNamespace(timestamp=600),
            SimpleNamespace(timestamp=300),
        ]
        with self.assertRaises(ValueError):
            _contiguous_segments(rows, 300)

    def test_invalid_timeframe_is_rejected(self):
        with self.assertRaises(ValueError):
            _contiguous_segments(candles(0, 2, 300), 0)


if __name__ == "__main__":
    unittest.main()
