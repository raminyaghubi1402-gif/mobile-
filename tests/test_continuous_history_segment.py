import unittest
from types import SimpleNamespace

from lili_trader.__main__ import _longest_contiguous_segment


class ContinuousHistorySegmentTests(unittest.TestCase):
    def test_selects_longest_segment_without_bridging_gap(self):
        candles = [
            SimpleNamespace(timestamp=0),
            SimpleNamespace(timestamp=300),
            SimpleNamespace(timestamp=600),
            SimpleNamespace(timestamp=1200),
            SimpleNamespace(timestamp=1500),
            SimpleNamespace(timestamp=1800),
            SimpleNamespace(timestamp=2100),
            SimpleNamespace(timestamp=2400),
        ]
        result = _longest_contiguous_segment(candles, 300)
        self.assertEqual([row.timestamp for row in result], [1200, 1500, 1800, 2100, 2400])

    def test_keeps_earliest_segment_when_lengths_tie(self):
        candles = [
            {"timestamp": 0},
            {"timestamp": 300},
            {"timestamp": 900},
            {"timestamp": 1200},
        ]
        result = _longest_contiguous_segment(candles, 300)
        self.assertEqual([row["timestamp"] for row in result], [0, 300])

    def test_empty_history_returns_empty_segment(self):
        self.assertEqual(_longest_contiguous_segment([], 300), [])

    def test_single_candle_is_not_discarded(self):
        candles = [SimpleNamespace(timestamp=600)]
        self.assertEqual(_longest_contiguous_segment(candles, 300), candles)


if __name__ == "__main__":
    unittest.main()
