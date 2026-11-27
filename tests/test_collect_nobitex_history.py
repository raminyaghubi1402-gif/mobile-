import unittest

from lili_trader.nobitex_data import NobitexCandle
from scripts.collect_nobitex_history import fetch_btcusdt_15m_window


INTERVAL = 900


def candle(timestamp):
    return NobitexCandle(
        symbol="BTCUSDT",
        resolution="15",
        timestamp=timestamp,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=1.0,
        fetched_at=2_000_000_000,
    )


class FakeAdapter:
    def __init__(self, range_rows, countback_rows):
        self.range_rows = list(range_rows)
        self.countback_rows = list(countback_rows)
        self.calls = []

    def fetch_recent(
        self, symbol, resolution, count, *, page, to_timestamp,
        from_timestamp=None,
    ):
        self.calls.append({
            "symbol": symbol,
            "resolution": resolution,
            "count": count,
            "page": page,
            "from_timestamp": from_timestamp,
            "to_timestamp": to_timestamp,
        })
        return list(self.range_rows if from_timestamp is not None else self.countback_rows)


class NobitexHistoryCollectorWindowTests(unittest.TestCase):
    def setUp(self):
        self.now = 1_800_000_000
        self.latest_closed = (self.now // INTERVAL) * INTERVAL - INTERVAL
        self.first_expected = self.latest_closed - 95 * INTERVAL
        self.expected = list(range(
            self.first_expected, self.latest_closed + INTERVAL, INTERVAL
        ))
        self.range_end = self.latest_closed + INTERVAL - 1

    def test_requests_through_last_second_of_latest_closed_candle(self):
        guard = candle(self.first_expected - INTERVAL)
        rows = [guard] + [candle(timestamp) for timestamp in self.expected]
        adapter = FakeAdapter(rows, [candle(timestamp) for timestamp in self.expected])

        result = fetch_btcusdt_15m_window(adapter, now=self.now)

        self.assertEqual([row.timestamp for row in result], self.expected)
        self.assertEqual(len(result), 96)
        self.assertEqual(adapter.calls[0]["from_timestamp"], self.first_expected - INTERVAL)
        self.assertEqual(adapter.calls[0]["to_timestamp"], self.range_end)
        self.assertEqual(adapter.calls[1]["to_timestamp"], self.range_end)
        self.assertTrue(all(row.timestamp <= self.latest_closed for row in result))

    def test_missing_source_candle_still_fails_without_synthetic_fill(self):
        missing = self.expected[-1]
        rows = [candle(self.first_expected - INTERVAL)] + [
            candle(timestamp) for timestamp in self.expected if timestamp != missing
        ]
        adapter = FakeAdapter(rows, [
            candle(timestamp) for timestamp in self.expected if timestamp != missing
        ])

        with self.assertRaisesRegex(RuntimeError, f"first_missing={missing}"):
            fetch_btcusdt_15m_window(adapter, now=self.now)

    def test_rejects_unaligned_source_timestamp(self):
        rows = [
            candle(self.first_expected - INTERVAL),
            candle(self.expected[0] + 1),
            *[candle(timestamp) for timestamp in self.expected[1:]],
        ]
        adapter = FakeAdapter(rows, [])

        with self.assertRaisesRegex(RuntimeError, "unaligned BTCUSDT 15m timestamp"):
            fetch_btcusdt_15m_window(adapter, now=self.now)


if __name__ == "__main__":
    unittest.main()
