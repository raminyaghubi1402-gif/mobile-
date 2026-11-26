import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from lili_trader.live_paper_monitor import (
    FEE_RATE,
    SLIPPAGE_RATE,
    LivePaperMonitorError,
    PaperState,
    latest_contiguous_closed_segment,
    _activate_pending,
    _committed_risk_quote,
    _manage_position,
    _validated_signal_levels,
)

TEST_PAPER_EQUITY_QUOTE = 27_100_000.0
from lili_trader.nobitex_data import NobitexCandle
from lili_trader.decision_journal import DecisionJournal
from lili_trader.nobitex_limits import (
    NOBITEX_BASE_SPOT_FEE_RATE_IRT,
    NOBITEX_MINIMUM_SPOT_ORDER_IRT,
)
from lili_trader.risk_management import fee_adjusted_breakeven_stop
from lili_trader.strategy_engines import StrategyDecision


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
        result = _activate_pending("BTCIRT", [candle(600)], self.state, equity_quote=TEST_PAPER_EQUITY_QUOTE)
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
        result = _activate_pending("BTCIRT", [gap], self.state, equity_quote=TEST_PAPER_EQUITY_QUOTE)
        self.assertIn("cancelled", result)
        self.assertNotIn("BTCIRT", self.state.data["positions"])

    def test_entry_is_cancelled_if_actual_reward_risk_falls_below_minimum(self):
        self.state.data["pending"]["BTCIRT"]["take_profit"] = 104.0
        result = _activate_pending("BTCIRT", [candle(600, close=101.0)], self.state, equity_quote=TEST_PAPER_EQUITY_QUOTE)
        self.assertIn("cancelled", result)
        self.assertNotIn("BTCIRT", self.state.data["positions"])

    def test_entry_below_nobitex_minimum_order_is_cancelled(self):
        self.state.data["pending"]["BTCIRT"]["risk_quote"] = 1.0
        result = _activate_pending("BTCIRT", [candle(600)], self.state, equity_quote=TEST_PAPER_EQUITY_QUOTE)
        self.assertIn("below_minimum_order", result)
        self.assertNotIn("BTCIRT", self.state.data["positions"])

    def test_live_fee_uses_nobitex_base_spot_rate(self):
        self.assertAlmostEqual(FEE_RATE, NOBITEX_BASE_SPOT_FEE_RATE_IRT)
        self.assertEqual(NOBITEX_MINIMUM_SPOT_ORDER_IRT, 3_000_000)

    def test_unknown_other_pending_allocation_blocks_new_entry(self):
        self.state.data["pending"]["BTCIRT"] = {
            "decision_id": "decision-btc-current",
            "signal_timestamp": 300,
            "entry_reference": 100.0,
            "stop_loss": 98.0,
            "take_profit": 106.0,
            "risk_quote": 135_500.0,
        }
        self.state.data["pending"]["ETHIRT"] = {
            "decision_id": "decision-eth-legacy",
            "signal_timestamp": 300,
            "stop_loss": 198.0,
            "take_profit": 206.0,
            "risk_quote": 135_500.0,
        }
        with self.assertRaises(LivePaperMonitorError):
            _activate_pending("BTCIRT", [candle(600)], self.state, equity_quote=TEST_PAPER_EQUITY_QUOTE)
        self.assertIn("BTCIRT", self.state.data["pending"])
        self.assertNotIn("BTCIRT", self.state.data["positions"])

    def test_two_symbols_share_one_paper_cash_budget(self):
        self.state.data["pending"]["BTCIRT"] = {
            "decision_id": "decision-btc-shared-cash",
            "signal_timestamp": 300,
            "entry_reference": 100.0,
            "stop_loss": 99.5,
            "take_profit": 102.0,
            "risk_quote": 135_500.0,
        }
        self.state.data["pending"]["ETHIRT"] = {
            "decision_id": "decision-eth-shared-cash",
            "signal_timestamp": 300,
            "entry_reference": 200.0,
            "stop_loss": 199.0,
            "take_profit": 204.0,
            "risk_quote": 135_500.0,
        }
        btc_next = NobitexCandle(
            symbol="BTCIRT", resolution="5", timestamp=600,
            open=100.0, high=101.0, low=99.8, close=100.5,
            volume=10.0, fetched_at=900,
        )
        eth_next = NobitexCandle(
            symbol="ETHIRT", resolution="5", timestamp=600,
            open=200.0, high=202.0, low=199.5, close=201.0,
            volume=10.0, fetched_at=900,
        )

        btc_result = _activate_pending("BTCIRT", [btc_next], self.state, equity_quote=TEST_PAPER_EQUITY_QUOTE)
        eth_result = _activate_pending("ETHIRT", [eth_next], self.state, equity_quote=TEST_PAPER_EQUITY_QUOTE)

        self.assertTrue(btc_result.startswith("paper_entry_at_next_open"))
        self.assertTrue(eth_result.startswith("paper_entry_at_next_open"))
        self.assertEqual(set(self.state.data["positions"]), {"BTCIRT", "ETHIRT"})
        reserved = sum(
            position["entry_fill_price"] * position["quantity"]
            + position["entry_fee_quote"]
            for position in self.state.data["positions"].values()
        )
        self.assertLessEqual(reserved, TEST_PAPER_EQUITY_QUOTE + 1e-6)
        self.assertAlmostEqual(
            self.state.data["positions"]["BTCIRT"]["entry_fee_quote"],
            self.state.data["positions"]["BTCIRT"]["entry_fill_price"]
            * self.state.data["positions"]["BTCIRT"]["quantity"] * FEE_RATE,
        )

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
        self.assertGreater(
            position["stop_loss"],
            fee_adjusted_breakeven_stop(
                105.105, fee_rate=FEE_RATE, slippage_rate=SLIPPAGE_RATE
            ),
        )
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

    def test_existing_outcome_reconciles_position_after_interrupted_state_save(self):
        entry_timestamp = 1_800_000_000
        exit_timestamp = entry_timestamp + 300
        state = PaperState("unused-state.json")
        state.data["positions"]["BTCIRT"] = {
            "trade_id": "paper-recovery-test",
            "decision_id": "decision-recovery-test",
            "entry_timestamp": entry_timestamp,
            "last_processed_timestamp": entry_timestamp,
            "raw_entry_price": 100.0,
            "entry_fill_price": 100.0 * (1 + SLIPPAGE_RATE),
            "quantity": 1.0,
            "stop_loss": 98.0,
            "take_profit": 120.0,
            "entry_fee_quote": 100.0 * (1 + SLIPPAGE_RATE) * FEE_RATE,
            "execution": "paper_only",
        }

        with tempfile.TemporaryDirectory() as directory:
            journal = DecisionJournal(Path(directory) / "decisions.jsonl")
            occurred_at = datetime.fromtimestamp(
                entry_timestamp, tz=timezone.utc
            ).isoformat()
            journal.append_decision(
                decision_id="decision-recovery-test",
                occurred_at=occurred_at,
                market_data_at=datetime.fromtimestamp(
                    entry_timestamp + 300, tz=timezone.utc
                ).isoformat(),
                symbol="BTCIRT",
                action="BUY",
                strategy="trend_pullback",
                strategy_version="test",
                reason="paper recovery test",
                evidence={"test": True},
                entry_price=100.0,
                stop_loss=98.0,
                take_profit=120.0,
                risk_quote=100.0,
                trade_id="paper-recovery-test",
            )

            entry_fill = 100.0 * (1 + SLIPPAGE_RATE)
            exit_fill = 98.0 * (1 - SLIPPAGE_RATE)
            gross = 98.0 - 100.0
            fees = (entry_fill + exit_fill) * FEE_RATE
            slippage = (entry_fill - 100.0) + (98.0 - exit_fill)
            net = gross - fees - slippage
            journal.append_outcome(
                decision_id="decision-recovery-test",
                occurred_at=occurred_at,
                trade_id="paper-recovery-test",
                side="BUY",
                entry_at=occurred_at,
                exit_at=datetime.fromtimestamp(
                    exit_timestamp + 300, tz=timezone.utc
                ).isoformat(),
                gross_pnl_quote=gross,
                fees_quote=fees,
                slippage_quote=slippage,
                net_pnl_quote=net,
                exit_reason="stop_loss",
            )

            stop_candle = NobitexCandle(
                symbol="BTCIRT", resolution="5", timestamp=exit_timestamp,
                open=99.0, high=101.0, low=97.0, close=99.0,
                volume=10.0, fetched_at=exit_timestamp + 300,
            )
            result = _manage_position(
                "BTCIRT", [stop_candle], state, journal,
                now=exit_timestamp + 300,
            )

            self.assertIn("closed_stop_loss", result)
            self.assertNotIn("BTCIRT", state.data["positions"])
            self.assertEqual(journal.summary()["outcomes"], 1)

    def test_conflicting_existing_outcome_keeps_position_and_fails_closed(self):
        entry_timestamp = 1_800_000_000
        exit_timestamp = entry_timestamp + 300
        state = PaperState("unused-state.json")
        state.data["positions"]["BTCIRT"] = {
            "trade_id": "paper-recovery-test",
            "decision_id": "decision-recovery-conflict",
            "entry_timestamp": entry_timestamp,
            "last_processed_timestamp": entry_timestamp,
            "raw_entry_price": 100.0,
            "entry_fill_price": 100.0 * (1 + SLIPPAGE_RATE),
            "quantity": 1.0,
            "stop_loss": 98.0,
            "take_profit": 120.0,
            "entry_fee_quote": 100.0 * (1 + SLIPPAGE_RATE) * FEE_RATE,
            "execution": "paper_only",
        }

        with tempfile.TemporaryDirectory() as directory:
            journal = DecisionJournal(Path(directory) / "decisions.jsonl")
            occurred_at = datetime.fromtimestamp(
                entry_timestamp, tz=timezone.utc
            ).isoformat()
            journal.append_decision(
                decision_id="decision-recovery-conflict",
                occurred_at=occurred_at,
                market_data_at=datetime.fromtimestamp(
                    entry_timestamp + 300, tz=timezone.utc
                ).isoformat(),
                symbol="BTCIRT",
                action="BUY",
                strategy="trend_pullback",
                strategy_version="test",
                reason="paper recovery conflict test",
                evidence={"test": True},
                entry_price=100.0,
                stop_loss=98.0,
                take_profit=120.0,
                risk_quote=100.0,
                trade_id="paper-recovery-test",
            )
            journal.append_outcome(
                decision_id="decision-recovery-conflict",
                occurred_at=occurred_at,
                trade_id="different-trade-id",
                side="BUY",
                entry_at=occurred_at,
                exit_at=datetime.fromtimestamp(
                    exit_timestamp + 300, tz=timezone.utc
                ).isoformat(),
                gross_pnl_quote=-2.0,
                fees_quote=0.5,
                slippage_quote=0.2,
                net_pnl_quote=-2.7,
                exit_reason="stop_loss",
            )
            stop_candle = NobitexCandle(
                symbol="BTCIRT", resolution="5", timestamp=exit_timestamp,
                open=99.0, high=101.0, low=97.0, close=99.0,
                volume=10.0, fetched_at=exit_timestamp + 300,
            )

            with self.assertRaisesRegex(
                LivePaperMonitorError, "existing journal outcome does not match"
            ):
                _manage_position(
                    "BTCIRT", [stop_candle], state, journal,
                    now=exit_timestamp + 300,
                )

            self.assertIn("BTCIRT", state.data["positions"])
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


class EngineSignalIsolationTests(unittest.TestCase):
    def test_valid_signal_levels_are_parsed_independently(self):
        decision = StrategyDecision(
            "breakout", "SIGNAL", "valid levels",
            {"entry_reference": 100.0, "stop_loss": 98.0, "take_profit": 103.0},
        )
        self.assertEqual(
            _validated_signal_levels(decision),
            {"entry_reference": 100.0, "stop_loss": 98.0, "take_profit": 103.0},
        )

    def test_invalid_or_boolean_signal_levels_are_rejected(self):
        for evidence in (
            {"entry_reference": 100, "stop_loss": 98},
            {"entry_reference": True, "stop_loss": 98, "take_profit": 104},
            {"entry_reference": 100, "stop_loss": 101, "take_profit": 104},
            {"entry_reference": 100, "stop_loss": 98, "take_profit": 102},
        ):
            decision = StrategyDecision("breakout", "SIGNAL", "bad levels", evidence)
            with self.assertRaises(ValueError):
                _validated_signal_levels(decision)


class EngineOwnedPaperPositionTests(unittest.TestCase):
    def test_two_engines_can_hold_separate_positions_for_same_symbol(self):
        state = PaperState("unused-state.json")
        for engine in ("trend_pullback", "volume_trading"):
            key = f"BTCIRT::{engine}"
            state.data["pending"][key] = {
                "symbol": "BTCIRT",
                "strategy": engine,
                "decision_id": f"decision-{engine}",
                "signal_timestamp": 300,
                "entry_reference": 100.0,
                "stop_loss": 98.0,
                "take_profit": 106.0,
                "risk_quote": 135_500.0,
            }
        result_a = _activate_pending(
            "BTCIRT", [candle(600)], state, equity_quote=TEST_PAPER_EQUITY_QUOTE,
            state_key="BTCIRT::trend_pullback",
        )
        result_b = _activate_pending(
            "BTCIRT", [candle(600)], state, equity_quote=TEST_PAPER_EQUITY_QUOTE,
            state_key="BTCIRT::volume_trading",
        )
        self.assertTrue(result_a.startswith("paper_entry_at_next_open"))
        self.assertTrue(result_b.startswith("paper_entry_at_next_open"))
        self.assertEqual(
            set(state.data["positions"]),
            {"BTCIRT::trend_pullback", "BTCIRT::volume_trading"},
        )
        self.assertEqual(state.data["positions"]["BTCIRT::trend_pullback"]["strategy"], "trend_pullback")
        self.assertEqual(state.data["positions"]["BTCIRT::volume_trading"]["strategy"], "volume_trading")
        reserved_cash = sum(
            p["entry_fill_price"] * p["quantity"] + p["entry_fee_quote"]
            for p in state.data["positions"].values()
        )
        self.assertLessEqual(reserved_cash, TEST_PAPER_EQUITY_QUOTE + 1e-6)

    def test_managing_one_engine_position_does_not_close_another(self):
        entry_ts = 1_800_000_000
        state = PaperState("unused-state.json")
        for engine, stop in (("trend_pullback", 99.0), ("volume_trading", 95.0)):
            state.data["positions"][f"BTCIRT::{engine}"] = {
                "symbol": "BTCIRT",
                "strategy": engine,
                "trade_id": f"trade-{engine}",
                "decision_id": f"decision-{engine}",
                "entry_timestamp": entry_ts,
                "last_processed_timestamp": entry_ts,
                "raw_entry_price": 100.0,
                "entry_fill_price": 100.1,
                "quantity": 1.0,
                "stop_loss": stop,
                "take_profit": 150.0,
                "entry_fee_quote": 0.1,
                "risk_quote": 1.0,
                "execution": "paper_only",
            }
        stop_bar = NobitexCandle(
            symbol="BTCIRT", resolution="5", timestamp=entry_ts + 300,
            open=100.0, high=101.0, low=98.0, close=99.5,
            volume=10.0, fetched_at=entry_ts + 600,
        )
        with tempfile.TemporaryDirectory() as directory:
            journal = DecisionJournal(Path(directory) / "journal.jsonl")
            occurred_at = datetime.fromtimestamp(entry_ts, tz=timezone.utc).isoformat()
            journal.append_decision(
                decision_id="decision-trend_pullback",
                occurred_at=occurred_at,
                market_data_at=datetime.fromtimestamp(entry_ts + 300, tz=timezone.utc).isoformat(),
                symbol="BTCIRT",
                action="BUY",
                strategy="trend_pullback",
                strategy_version="test",
                reason="isolated position lifecycle test",
                evidence={"test": True},
                entry_price=100.0,
                stop_loss=99.0,
                take_profit=150.0,
                risk_quote=1.0,
                timeframe="5m",
                trade_id="trade-trend_pullback",
            )
            result = _manage_position(
                "BTCIRT", [stop_bar], state, journal, now=entry_ts + 600,
                state_key="BTCIRT::trend_pullback",
            )
            self.assertIn("closed_stop_loss", result)
            self.assertNotIn("BTCIRT::trend_pullback", state.data["positions"])
            self.assertIn("BTCIRT::volume_trading", state.data["positions"])

    def test_portfolio_risk_is_scoped_and_sum_of_symbol_caps_is_bounded(self):
        state = PaperState("unused-state.json")
        state.data["positions"]["BTCIRT::trend_pullback"] = {"symbol": "BTCIRT", "risk_quote": 40.0}
        state.data["pending"]["BTCIRT::volume_trading"] = {"symbol": "BTCIRT", "risk_quote": 20.0}
        state.data["positions"]["ETHIRT::breakout"] = {"symbol": "ETHIRT", "risk_quote": 30.0}
        self.assertEqual(_committed_risk_quote(state, symbol="BTCIRT"), 60.0)
        self.assertEqual(_committed_risk_quote(state, symbol="ETHIRT"), 30.0)
        self.assertEqual(_committed_risk_quote(state), 90.0)
        equity = 10_000.0
        total_cap = equity * 0.005
        per_symbol_cap = total_cap / 2
        self.assertLessEqual(per_symbol_cap * 2, total_cap)

    def test_schema_one_state_migrates_without_dropping_existing_positions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(
                '{"schema_version":1,"pending":{"BTCIRT":{"strategy":"ict","decision_id":"p"}},'
                '"positions":{"ETHIRT":{"trade_id":"legacy-trade","entry_fill_price":10,'
                '"quantity":1,"stop_loss":9}}}',
                encoding="utf-8",
            )
            state = PaperState(path)
            self.assertEqual(state.data["schema_version"], 2)
            self.assertIn("BTCIRT::ict", state.data["pending"])
            self.assertIn("ETHIRT::legacy", state.data["positions"])
            self.assertEqual(state.data["positions"]["ETHIRT::legacy"]["trade_id"], "legacy-trade")

class PaperStateTests(unittest.TestCase):
    def test_state_round_trips_engine_owned_pending_and_positions(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            state = PaperState(path)
            state.data["pending"]["BTCIRT::legacy"] = {
                "symbol": "BTCIRT", "strategy": "legacy", "decision_id": "x"
            }
            state.data["positions"]["ETHIRT::legacy"] = {
                "symbol": "ETHIRT", "strategy": "legacy", "trade_id": "y"
            }
            state.save()
            loaded = PaperState(path)
            self.assertEqual(loaded.data["pending"]["BTCIRT::legacy"]["decision_id"], "x")
            self.assertEqual(loaded.data["positions"]["ETHIRT::legacy"]["trade_id"], "y")

    def test_schema_two_rejects_slot_record_for_another_symbol(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            path.write_text(
                '{"schema_version":2,"pending":{"BTCIRT::trend_pullback":'
                '{"symbol":"ETHIRT","strategy":"trend_pullback"}},"positions":{}}',
                encoding="utf-8",
            )
            with self.assertRaises(LivePaperMonitorError):
                PaperState(path)

    def test_schema_two_rejects_non_object_engine_records(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            path.write_text(
                '{"schema_version":2,"pending":{"BTCIRT::trend_pullback":null},"positions":{}}',
                encoding="utf-8",
            )
            with self.assertRaises(LivePaperMonitorError):
                PaperState(path)

    def test_legacy_migration_rejects_symbol_mismatch(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            path.write_text(
                '{"schema_version":1,"pending":{"BTCIRT":{"symbol":"ETHIRT","decision_id":"x"}},'
                '"positions":{}}',
                encoding="utf-8",
            )
            with self.assertRaises(LivePaperMonitorError):
                PaperState(path)

    def test_corrupt_state_is_not_silently_reset(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            path.write_text("{broken", encoding="utf-8")
            with self.assertRaises(LivePaperMonitorError):
                PaperState(path)


if __name__ == "__main__":
    unittest.main()
