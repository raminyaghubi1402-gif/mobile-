import unittest

from lili_trader.nobitex_data import NobitexAdapter, NobitexCandle
from scripts.collect_nobitex_history import (
    MAX_CANDLE_AGE_SECONDS,
    SYMBOL_FETCH_COUNTS,
    closed_fresh_candles,
)


class NobitexHistoryCollectorTests(unittest.TestCase):
    @staticmethod
    def candle(
        timestamp,
        *,
        symbol="BTCIRT",
        resolution="15",
        fetched_at=None,
    ):
        interval = NobitexAdapter.RESOLUTION_SECONDS[resolution]
        return NobitexCandle(
            symbol=symbol,
            resolution=resolution,
            timestamp=timestamp,
            open=100.0,
            high=102.0,
            low=99.0,
            close=101.0,
            volume=1.0,
            fetched_at=timestamp + interval if fetched_at is None else fetched_at,
        )

    def test_collector_scope_is_only_supported_irt_markets_and_live_timeframes(self):
        expected = {
            "BTCIRT": {"5": 500, "15": 500, "60": 500},
            "ETHIRT": {"5": 500, "15": 500, "60": 500},
        }
        self.assertEqual(SYMBOL_FETCH_COUNTS, expected)
        self.assertEqual(set(SYMBOL_FETCH_COUNTS), {"BTCIRT", "ETHIRT"})
        self.assertEqual(MAX_CANDLE_AGE_SECONDS, {"5": 900, "15": 1800, "60": 7200})

    def test_unclosed_latest_candle_is_excluded(self):
        now = 1_800_000_000
        interval = NobitexAdapter.RESOLUTION_SECONDS["15"]
        closed_older = self.candle(now - 2 * interval)
        closed_latest = self.candle(now - interval)
        unclosed = self.candle(now, fetched_at=now + 120)

        result = closed_fresh_candles(
            [closed_older, closed_latest, unclosed],
            symbol="BTCIRT",
            resolution="15",
            now=now,
        )

        self.assertEqual(
            [row.timestamp for row in result],
            [closed_older.timestamp, closed_latest.timestamp],
        )
        self.assertTrue(all(
            row.timestamp + interval <= row.fetched_at
            and row.timestamp + interval <= now
            for row in result
        ))

    def test_stale_latest_closed_candle_fails_closed(self):
        now = 1_800_000_000
        stale = self.candle(now - 4 * NobitexAdapter.RESOLUTION_SECONDS["15"])

        with self.assertRaisesRegex(RuntimeError, "latest closed candle is stale"):
            closed_fresh_candles(
                [stale],
                symbol="BTCIRT",
                resolution="15",
                now=now,
            )

    def test_no_closed_source_candle_fails_closed(self):
        now = 1_800_000_000
        unclosed = self.candle(now, fetched_at=now + 30)

        with self.assertRaisesRegex(RuntimeError, "no fully closed source candles"):
            closed_fresh_candles(
                [unclosed],
                symbol="BTCIRT",
                resolution="15",
                now=now,
            )

    def test_wrong_symbol_resolution_and_duplicate_timestamps_are_rejected(self):
        now = 1_800_000_000
        valid = self.candle(now - 900)

        with self.assertRaisesRegex(ValueError, "wrong symbol or resolution"):
            closed_fresh_candles(
                [self.candle(now - 900, symbol="BTCUSDT")],
                symbol="BTCIRT",
                resolution="15",
                now=now,
            )

        with self.assertRaisesRegex(ValueError, "wrong symbol or resolution"):
            closed_fresh_candles(
                [self.candle(now - 900, resolution="5")],
                symbol="BTCIRT",
                resolution="15",
                now=now,
            )

        with self.assertRaisesRegex(ValueError, "duplicate timestamp"):
            closed_fresh_candles(
                [valid, valid],
                symbol="BTCIRT",
                resolution="15",
                now=now,
            )


if __name__ == "__main__":
    unittest.main()
