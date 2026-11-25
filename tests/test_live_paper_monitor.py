import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from lili_trader.live_paper_monitor import (
    LivePaperMonitorError,
    PaperState,
    latest_contiguous_closed_segment,
    _activate_pending,
    _manage_position,
)
from lili_trader.nobitex_data import NobitexCandle
from lili_trader.decision_journal import DecisionJournal


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


    def test_open_position_raises_stop_only_after_confirmed_higher_low(self):
        rows = [
            (100, 103, 99, 102), (102, 105, 101, 104),
            (104, 106, 102, 103), (103, 104, 100, 101),
            (101, 103, 99, 102), (102, 106, 101, 105),
            (105, 108, 104, 107), (107, 109, 105, 106),
            (106, 108, 103, 104), (104, 107, 102, 106),
            (106, 110, 105, 109), (109, 112, 109, 111),
            (111, 113, 110, 112), (112, 114, 108, 111),
            (111, 115, 110, 114), (114, 117, 112, 116),
            (116, 118, 113, 115), (115, 117, 112, 116),
            (116, 120, 115, 119), (119, 122, 117, 121),
        ]
        rows = [
            NobitexCandle(
                symbol="BTCIRT", resolution="5",
                timestamp=1_800_000_000 + i * 300,
                open=o, high=h, low=lo, close=c, volume=10.0,
                fetched_at=1_800_000_000 + i * 300 + 300,
            )
            for i, (o, h, lo, c) in enumerate(rows)
        ]
        state = PaperState("unused-state.json")
        entry_index = 5
        state.data["positions"]["BTCIRT"] = {
            "trade_id": "paper-trailing-test",
            "decision_id": "decision-trailing-test",
            "entry_timestamp": rows[entry_index].timestamp,
            "last_processed_timestamp": rows[entry_index - 1].timestamp,
            "raw_entry_price": 105.0,
            "entry_fill_price": 105.105,
            "quantity": 1.0,
            "stop_loss": 98.0,
            "take_profit": 1_000.0,
            "entry_fee_quote": 0.105105,
            "execution": "paper_only",
        }
        journal = DecisionJournal("unused-journal.jsonl")
        event_time = datetime.fromtimestamp(
            rows[0].timestamp + 300, tz=timezone.utc
        ).isoformat()
        journal.append_decision(
            decision_id="decision-trailing-test",
            occurred_at=event_time,
            market_data_at=event_time,
            symbol="BTCIRT",
            action="BUY",
            strategy="trend_pullback",
            strategy_version="test",
            reason="valid paper test setup",
            evidence={"test": True},
            entry_price=105.0,
            stop_loss=98.0,
            take_profit=1_000.0,
            risk_quote=100.0,
            trade_id="paper-trailing-test",
        )
        result = _manage_position(
            "BTCIRT", rows, state, journal, now=rows[-1].timestamp + 300
        )
        position = state.data["positions"]["BTCIRT"]
        self.assertIn("trailing_stop_raised", result)
        self.assertGreater(position["stop_loss"], 98.0)
        self.assertGreater(position["stop_loss"], 105.105 * 1.001 / (0.999 * 0.999))
        self.assertEqual(position["last_processed_timestamp"], rows[-1].timestamp)
        self.assertEqual(position["execution"], "paper_only")

        trailing_stop = position["stop_loss"]
        next_timestamp = rows[-1].timestamp + 300
        stop_candle = NobitexCandle(
            symbol="BTCIRT", resolution="5", timestamp=next_timestamp,
            open=108.0, high=110.0, low=trailing_stop - 0.1, close=108.5,
            volume=10.0, fetched_at=next_timestamp + 300,
        )
        close_result = _manage_position(
            "BTCIRT", rows + [stop_candle], state, journal,
            now=next_timestamp + 300,
        )
        self.assertIn("closed_stop_loss", close_result)
        self.assertNotIn("BTCIRT", state.data["positions"])
        self.assertEqual(journal.summary()["outcomes"], 1)

    def test_unclosed_confirmation_candle_cannot_raise_stop(self):
        rows = [
            (100, 103, 99, 102), (102, 105, 101, 104),
            (104, 106, 102, 103), (103, 104, 100, 101),
            (101, 103, 99, 102), (102, 106, 101, 105),
            (105, 108, 104, 107), (107, 109, 105, 106),
            (106, 108, 103, 104), (104, 107, 102, 106),
            (106, 110, 105, 109), (109, 112, 109, 111),
            (111, 113, 110, 112), (112, 114, 105, 111),
            (111, 115, 110, 114), (114, 117, 112, 116),
            (116, 118, 113, 115), (115, 117, 111, 116),
            (116, 120, 115, 119), (119, 122, 117, 121),
        ]
        rows = [
            NobitexCandle(
                symbol="BTCIRT", resolution="5",
                timestamp=1_800_000_000 + i * 300,
                open=o, high=h, low=lo, close=c, volume=10.0,
                fetched_at=1_800_000_000 + i * 300 + (299 if i == 19 else 300),
            )
            for i, (o, h, lo, c) in enumerate(rows)
        ]
        state = PaperState("unused-state.json")
        state.data["positions"]["BTCIRT"] = {
            "trade_id": "paper-unclosed-test", "decision_id": "decision-unclosed-test",
            "entry_timestamp": rows[5].timestamp,
            "last_processed_timestamp": rows[4].timestamp,
            "raw_entry_price": 105.0, "entry_fill_price": 105.105,
            "quantity": 1.0, "stop_loss": 98.0, "take_profit": 1_000.0,
            "entry_fee_quote": 0.105105, "execution": "paper_only",
        }
        _manage_position(
            "BTCIRT", rows, state, DecisionJournal("unused-journal.jsonl"),
            now=rows[-1].timestamp + 299,
        )
        self.assertEqual(state.data["positions"]["BTCIRT"]["stop_loss"], 98.0)
        self.assertEqual(state.data["positions"]["BTCIRT"]["last_processed_timestamp"], rows[-2].timestamp)


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
