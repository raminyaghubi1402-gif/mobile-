import unittest

from lili_trader.engine_snapshot_builder import build_independent_engine_snapshots
from lili_trader.nobitex_data import NobitexCandle


def candle(timestamp, resolution, close, *, fetched_at=None):
    seconds = {"5": 300, "15": 900, "60": 3600}[resolution]
    return NobitexCandle(
        symbol="BTCIRT", resolution=resolution, timestamp=timestamp,
        open=close, high=close + 2, low=close - 2, close=close,
        volume=10.0, fetched_at=timestamp + seconds if fetched_at is None else fetched_at,
    )


class EngineSnapshotBuilderTests(unittest.TestCase):
    def setUp(self):
        self.now = 1_800_010_000
        self.five = [
            candle(self.now - 6300 + i * 300, "5", 100 + i)
            for i in range(21)
        ]
        self.fifteen = [
            candle(self.now - 18_000 + i * 900, "15", 100 + (i % 5))
            for i in range(20)
        ]
        self.hourly = [
            candle(self.now - 120_000 + i * 3600, "60", 100 + i)
            for i in range(30)
        ]

    def test_builds_separate_snapshots_for_all_four_implemented_engines(self):
        snapshots = build_independent_engine_snapshots(
            self.hourly, self.fifteen, self.five, now=self.now
        )
        self.assertEqual(
            set(snapshots),
            {"trend_pullback", "breakout", "breakout_retest",
             "support_resistance_rejection", "volume_trading"},
        )
        self.assertEqual(
            snapshots["breakout"]["latest_5m"]["timestamp"],
            self.five[-1].timestamp,
        )
        self.assertEqual(
            snapshots["breakout_retest"]["pre_breakout_5m"]["timestamp"],
            self.five[-3].timestamp,
        )
        self.assertEqual(
            snapshots["support_resistance_rejection"]["previous_5m"]["timestamp"],
            self.five[-2].timestamp,
        )
        self.assertEqual(len(snapshots["volume_trading"]["volume_history_5m"]), 20)

    def test_missing_zones_are_not_replaced_with_guessed_levels(self):
        flat_fifteen = [
            NobitexCandle(
                symbol="BTCIRT", resolution="15", timestamp=c.timestamp,
                open=100, high=101, low=99, close=100, volume=10,
                fetched_at=c.fetched_at,
            ) for c in self.fifteen
        ]
        snapshots = build_independent_engine_snapshots(
            self.hourly, flat_fifteen, self.five, now=self.now
        )
        self.assertIsNone(snapshots["breakout"]["resistance_zone"])
        self.assertIsNone(snapshots["support_resistance_rejection"]["support_zone"])

    def test_gap_in_three_required_5m_candles_fails_closed(self):
        broken = list(self.five)
        broken[-1] = candle(broken[-1].timestamp + 300, "5", 103)
        with self.assertRaises(ValueError):
            build_independent_engine_snapshots(
                self.hourly, self.fifteen, broken, now=self.now
            )

    def test_unclosed_or_stale_5m_candles_fail_closed(self):
        unclosed = list(self.five)
        latest = unclosed[-1]
        unclosed[-1] = candle(
            latest.timestamp, "5", latest.close,
            fetched_at=latest.timestamp + 299,
        )
        with self.assertRaises(ValueError):
            build_independent_engine_snapshots(
                self.hourly, self.fifteen, unclosed, now=self.now
            )

        stale = list(self.five)
        old = stale[-3]
        stale[-3] = candle(
            old.timestamp, "5", old.close,
            fetched_at=old.timestamp + 300,
        )
        with self.assertRaises(ValueError):
            build_independent_engine_snapshots(
                self.hourly, self.fifteen, stale, now=self.now + 900
            )


if __name__ == "__main__":
    unittest.main()
