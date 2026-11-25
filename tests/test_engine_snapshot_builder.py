import unittest
from types import SimpleNamespace
from unittest.mock import patch

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
            candle(self.now - 7200 + i * 300, "5", 100 + i)
            for i in range(24)
        ]
        self.fifteen = [
            candle(self.now - 18_000 + i * 900, "15", 100 + (i % 5))
            for i in range(20)
        ]
        self.hourly = [
            candle(self.now - 120_000 + i * 3600, "60", 100 + i)
            for i in range(30)
        ]

    def test_builds_separate_snapshots_for_all_registered_engines(self):
        snapshots = build_independent_engine_snapshots(
            self.hourly, self.fifteen, self.five, now=self.now
        )
        self.assertEqual(
            set(snapshots),
            {"trend_pullback", "breakout", "breakout_retest",
             "support_resistance_rejection", "volume_trading", "ict"},
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
        self.assertEqual(len(snapshots["ict"]["reference_5m"]), 20)
        self.assertEqual(snapshots["ict"]["retest_5m"]["timestamp"], self.five[-1].timestamp)

    def test_missing_trend_pullback_evidence_does_not_block_other_engines(self):
        with patch(
            "lili_trader.live_paper_monitor.build_trend_pullback_snapshot",
            side_effect=ValueError("trend-only structure unavailable"),
        ):
            snapshots = build_independent_engine_snapshots(
                self.hourly, self.fifteen, self.five, now=self.now
            )
        self.assertFalse(snapshots["trend_pullback"]["pullback_active"])
        self.assertIn("breakout", snapshots)
        self.assertIn("volume_trading", snapshots)
        self.assertIn("ict", snapshots)

    def test_short_ict_history_disables_only_ict_snapshot(self):
        snapshots = build_independent_engine_snapshots(
            self.hourly, self.fifteen, self.five[-21:], now=self.now
        )
        self.assertEqual(snapshots["ict"]["reference_5m"], [])
        self.assertIsNone(snapshots["ict"]["retest_5m"])
        self.assertEqual(len(snapshots["volume_trading"]["volume_history_5m"]), 20)

    def test_short_volume_history_disables_only_volume_and_ict_evidence(self):
        snapshots = build_independent_engine_snapshots(
            self.hourly, self.fifteen, self.five[-10:], now=self.now
        )
        self.assertIn("breakout", snapshots)
        self.assertIn("support_resistance_rejection", snapshots)
        self.assertEqual(snapshots["volume_trading"]["volume_history_5m"], [])
        self.assertEqual(snapshots["ict"]["reference_5m"], [])
        self.assertIsNone(snapshots["ict"]["liquidity_sweep_5m"])

    def test_zone_detection_failure_only_removes_zone_dependent_evidence(self):
        with patch(
            "lili_trader.engine_snapshot_builder.find_support_resistance",
            side_effect=RuntimeError("zone evidence unavailable"),
        ):
            snapshots = build_independent_engine_snapshots(
                self.hourly, self.fifteen, self.five, now=self.now
            )
        self.assertIsNone(snapshots["breakout"]["resistance_zone"])
        self.assertIsNone(snapshots["support_resistance_rejection"]["support_zone"])
        self.assertEqual(len(snapshots["volume_trading"]["volume_history_5m"]), 20)
        self.assertIn("ict", snapshots)

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

    def test_fetch_time_slightly_after_observation_does_not_mark_closed_bar_future(self):
        rows = list(self.five)
        latest = rows[-1]
        rows[-1] = candle(
            latest.timestamp, "5", latest.close,
            fetched_at=self.now + 2,
        )
        snapshots = build_independent_engine_snapshots(
            self.hourly, self.fifteen, rows, now=self.now
        )
        self.assertEqual(
            snapshots["breakout"]["latest_5m"]["timestamp"],
            latest.timestamp,
        )

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



    def test_range_hourly_falls_back_to_fifteen_minute_for_all_engine_snapshots(self):
        trend_15m = SimpleNamespace(
            direction="up",
            last_two_swing_highs=(110, 120),
            last_two_swing_lows=(100, 110),
        )
        trend_1h = SimpleNamespace(
            direction="range",
            last_two_swing_highs=(110, 112),
            last_two_swing_lows=(100, 98),
        )
        trend_snapshot = {
            "context": {"trend_1h": "range", "trend_15m": "up"},
            "structure_5m": {"bias": "up"},
            "regime": "range",
        }
        with patch(
            "lili_trader.engine_snapshot_builder.classify_price_action_trend",
            side_effect=[trend_15m, trend_1h],
        ), patch(
            "lili_trader.live_paper_monitor.build_trend_pullback_snapshot",
            return_value=trend_snapshot,
        ):
            snapshots = build_independent_engine_snapshots(
                self.hourly, self.fifteen, self.five, now=self.now
            )
        self.assertEqual(snapshots["breakout"]["regime"], "bullish")
        self.assertEqual(snapshots["trend_pullback"]["regime"], "bullish")
        self.assertEqual(snapshots["trend_pullback"]["context"]["primary_trend"], "up")
        self.assertEqual(
            snapshots["trend_pullback"]["context"]["primary_trend_timeframe"], "15m"
        )


    def test_trend_pullback_failure_preserves_range_hourly_fallback_for_other_engines(self):
        trend_15m = SimpleNamespace(
            direction="up",
            last_two_swing_highs=(110, 120),
            last_two_swing_lows=(100, 110),
        )
        trend_1h = SimpleNamespace(
            direction="range",
            last_two_swing_highs=(110, 112),
            last_two_swing_lows=(100, 98),
        )
        with patch(
            "lili_trader.engine_snapshot_builder.classify_price_action_trend",
            side_effect=[trend_15m, trend_1h],
        ), patch(
            "lili_trader.live_paper_monitor.build_trend_pullback_snapshot",
            side_effect=RuntimeError("trend-pullback evidence unavailable"),
        ):
            snapshots = build_independent_engine_snapshots(
                self.hourly, self.fifteen, self.five, now=self.now
            )
        self.assertEqual(snapshots["breakout"]["trend_1h"], "range")
        self.assertEqual(snapshots["breakout"]["trend_15m"], "up")
        self.assertEqual(snapshots["breakout"]["primary_trend"], "up")
        self.assertEqual(snapshots["breakout"]["primary_trend_timeframe"], "15m")
        self.assertEqual(snapshots["breakout"]["regime"], "bullish")
        self.assertEqual(
            snapshots["trend_pullback"]["context"]["trend_1h"], "range"
        )

if __name__ == "__main__":
    unittest.main()
