import unittest

from lili_trader.nobitex_data import NobitexCandle
from lili_trader.timeframe_resample import (
    aggregate_five_to_fifteen,
    extend_fifteen_with_five,
)


def candle(timestamp, resolution="5", open_=100.0, high=102.0,
           low=99.0, close=101.0, volume=1.0):
    return NobitexCandle(
        symbol="BTCIRT", resolution=resolution, timestamp=timestamp,
        open=open_, high=high, low=low, close=close, volume=volume,
        fetched_at=1,
    )


class TimeframeResampleTests(unittest.TestCase):
    def test_aggregates_only_complete_aligned_three_bar_groups(self):
        five = [
            candle(0, open_=100, high=103, low=99, close=102, volume=1),
            candle(300, open_=102, high=105, low=101, close=104, volume=2),
            candle(600, open_=104, high=106, low=103, close=105, volume=3),
            candle(900, open_=105, high=107, low=104, close=106, volume=4),
            candle(1500, open_=106, high=108, low=105, close=107, volume=5),
        ]
        fifteen = aggregate_five_to_fifteen(five)
        self.assertEqual(len(fifteen), 1)
        self.assertEqual(fifteen[0].timestamp, 0)
        self.assertEqual(fifteen[0].open, 100)
        self.assertEqual(fifteen[0].high, 106)
        self.assertEqual(fifteen[0].low, 99)
        self.assertEqual(fifteen[0].close, 105)
        self.assertEqual(fifteen[0].volume, 6)

    def test_extends_native_history_with_older_contiguous_derived_bars(self):
        five = [candle(i * 300, open_=100+i, high=102+i,
                        low=99+i, close=101+i) for i in range(12)]
        native = [
            candle(1800, "15", open_=200, high=205, low=198, close=203),
            candle(2700, "15", open_=203, high=207, low=201, close=206),
        ]
        merged = extend_fifteen_with_five(native, five)
        self.assertEqual([row.timestamp for row in merged],
                         [0, 900, 1800, 2700])
        # Native 15m values win on the overlapping timestamps.
        self.assertEqual(merged[2].open, 200)
        self.assertEqual(merged[3].close, 206)

    def test_does_not_bridge_a_gap_between_derived_and_native_history(self):
        five = [candle(i * 300) for i in range(9)]
        native = [candle(3600, "15")]
        merged = extend_fifteen_with_five(native, five)
        self.assertEqual([row.timestamp for row in merged], [3600])

    def test_rejects_non_five_minute_source(self):
        with self.assertRaises(ValueError):
            aggregate_five_to_fifteen([candle(0, "15")])


if __name__ == "__main__":
    unittest.main()
