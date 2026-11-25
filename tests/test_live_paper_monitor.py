import tempfile
import unittest
from pathlib import Path

from lili_trader.live_paper_monitor import (
    LivePaperMonitorError,
    PaperState,
    latest_contiguous_closed_segment,
    _activate_pending,
)
from lili_trader.nobitex_data import NobitexCandle


def candle(ts, resolution="5", close=100.0):
    return NobitexCandle(
        symbol="BTCIRT", resolution=resolution, timestamp=ts,
        open=close, high=close + 1, low=close - 1, close=close,
        volume=10.0, fetched_at=ts + 300,
    )


class LatestClosedSegmentTests(unittest.TestCase):
    def test_uses_latest_contiguous_segment_without_bridging_gap(self):
        rows = [candle(300), candle(600), candle(1200), candle(1500)]
        result = latest_contiguous_closed_segment(
            rows, resolution_seconds=300, now=1800,
            minimum_bars=2, maximum_age_seconds=600,
        )
        self.assertEqual([item.timestamp for item in result], [1200, 1500])

    def test_drops_unclosed_candle_and_rejects_stale_latest_bar(self):
        rows = [candle(300), candle(600), candle(900)]
        result = latest_contiguous_closed_segment(
            rows, resolution_seconds=300, now=1199,
            minimum_bars=2, maximum_age_seconds=600,
        )
        self.assertEqual([item.timestamp for item in result], [300, 600])
        with self.assertRaises(LivePaperMonitorError):
            latest_contiguous_closed_segment(
                rows, resolution_seconds=300, now=3000,
                minimum_bars=2, maximum_age_seconds=600,
            )

    def test_conflicting_duplicate_timestamp_is_rejected(self):
        with self.assertRaises(LivePaperMonitorError):
            latest_contiguous_closed_segment(
                [candle(300), candle(300, close=101)],
                resolution_seconds=300, now=1000,
                minimum_bars=1, maximum_age_seconds=600,
            )

    def test_insufficient_latest_segment_is_rejected(self):
        with self.assertRaises(LivePaperMonitorError):
            latest_contiguous_closed_segment(
                [candle(300), candle(900)],
                resolution_seconds=300, now=1200,
                minimum_bars=2, maximum_age_seconds=600,
            )


class PaperEntryTests(unittest.TestCase):
    def setUp(self):
        self.state = PaperState("unused-state.json")
        self.state.data["pending"]["BTCIRT"] = {
            "decision_id": "decision-1",
            "signal_timestamp": 300,
            "stop_loss": 98.0,
            "take_profit": 106.0,
            "risk_quote": 135500.0,
        }

    def test_entry_uses_the_next_candle_open_and_paper_only_state(self):
        result = _activate_pending("BTCIRT", [candle(600)], self.state)
        self.assertTrue(result.startswith("paper_entry_at_next_open"))
        position = self.state.data["positions"]["BTCIRT"]
        self.assertEqual(position["raw_entry_price"], 100.0)
        self.assertEqual(position["execution"], "paper_only")
        self.assertNotIn("BTCIRT", self.state.data["pending"])

    def test_entry_is_cancelled_if_next_open_gaps_beyond_target(self):
        gap = NobitexCandle(
            symbol="BTCIRT", resolution="5", timestamp=600,
            open=110.0, high=111.0, low=109.0, close=110.0,
            volume=10.0, fetched_at=900,
        )
        result = _activate_pending("BTCIRT", [gap], self.state)
        self.assertIn("cancelled", result)
        self.assertNotIn("BTCIRT", self.state.data["positions"])

    def test_entry_is_cancelled_if_actual_reward_risk_falls_below_minimum(self):
        self.state.data["pending"]["BTCIRT"]["take_profit"] = 104.0
        result = _activate_pending("BTCIRT", [candle(600, close=101.0)], self.state)
        self.assertIn("cancelled", result)
        self.assertNotIn("BTCIRT", self.state.data["positions"])


class PaperStateTests(unittest.TestCase):
    def test_state_round_trips_pending_and_positions(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            state = PaperState(path)
            state.data["pending"]["BTCIRT"] = {"decision_id": "x"}
            state.data["positions"]["ETHIRT"] = {"trade_id": "y"}
            state.save()
            loaded = PaperState(path)
            self.assertEqual(loaded.data["pending"]["BTCIRT"]["decision_id"], "x")
            self.assertEqual(loaded.data["positions"]["ETHIRT"]["trade_id"], "y")

    def test_corrupt_state_is_not_silently_reset(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            path.write_text("{broken", encoding="utf-8")
            with self.assertRaises(LivePaperMonitorError):
                PaperState(path)


if __name__ == "__main__":
    unittest.main()
