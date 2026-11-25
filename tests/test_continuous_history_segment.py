import unittest
from types import SimpleNamespace

from lili_trader.__main__ import (
    _build_contiguous_backtest_windows,
    _contiguous_segments,
    _longest_contiguous_segment,
)


def candles(start, count, step):
    return [SimpleNamespace(timestamp=start + index * step) for index in range(count)]


class ContinuousHistorySegmentTests(unittest.TestCase):
    def test_returns_every_segment_without_bridging_gaps(self):
        rows = candles(0, 3, 300) + candles(1200, 2, 300) + candles(2400, 3, 300)
        segments = _contiguous_segments(rows, 300)
        self.assertEqual(
            [[row.timestamp for row in segment] for segment in segments],
            [[0, 300, 600], [1200, 1500], [2400, 2700, 3000]],
        )

    def test_longest_helper_remains_backward_compatible(self):
        rows = candles(0, 3, 300) + candles(1200, 5, 300)
        result = _longest_contiguous_segment(rows, 300)
        self.assertEqual([row.timestamp for row in result], [1200, 1500, 1800, 2100, 2400])

    def test_keeps_earliest_segment_when_lengths_tie(self):
        rows = [{"timestamp": 0}, {"timestamp": 300}, {"timestamp": 900}, {"timestamp": 1200}]
        result = _longest_contiguous_segment(rows, 300)
        self.assertEqual([row["timestamp"] for row in result], [0, 300])

    def test_empty_history_returns_no_segments(self):
        self.assertEqual(_contiguous_segments([], 300), [])
        self.assertEqual(_longest_contiguous_segment([], 300), [])

    def test_single_candle_is_preserved_as_a_segment(self):
        rows = candles(600, 1, 300)
        self.assertEqual(_contiguous_segments(rows, 300), [rows])

    def test_invalid_timeframe_is_rejected(self):
        with self.assertRaises(ValueError):
            _contiguous_segments(candles(0, 2, 300), 0)

    def test_backtest_runs_each_overlapping_five_minute_segment_independently(self):
        base = 1800000000
        hourly = candles(base - 10 * 3600, 20, 3600)
        fifteen = candles(base - 6 * 3600, 60, 900)
        five = candles(base, 10, 300) + candles(base + 3600, 10, 300)

        windows = _build_contiguous_backtest_windows(hourly, fifteen, five)

        self.assertEqual(len(windows), 2)
        self.assertEqual(
            [[row.timestamp for row in window[2]] for window in windows],
            [list(range(base, base + 3000, 300)),
             list(range(base + 3600, base + 6600, 300))],
        )
        self.assertTrue(all(len(window[0]) >= 10 for window in windows))
        self.assertTrue(all(len(window[1]) >= 20 for window in windows))

    def test_higher_timeframe_gap_splits_five_minute_history_too(self):
        hourly = candles(0, 10, 3600) + candles(12 * 3600, 10, 3600)
        fifteen = candles(0, 88, 900)
        five = candles(0, 264, 300)

        windows = _build_contiguous_backtest_windows(hourly, fifteen, five)

        self.assertEqual(len(windows), 2)
        self.assertEqual(windows[0][2][0].timestamp, 0)
        self.assertEqual(windows[0][2][-1].timestamp, 10 * 3600 - 300)
        self.assertEqual(windows[1][2][0].timestamp, 12 * 3600)
        self.assertEqual(windows[1][2][-1].timestamp, 22 * 3600 - 300)

    def test_insufficient_timeframe_warmup_is_not_run(self):
        windows = _build_contiguous_backtest_windows(
            candles(0, 9, 3600),
            candles(0, 20, 900),
            candles(0, 10, 300),
        )
        self.assertEqual(windows, [])


if __name__ == "__main__":
    unittest.main()
