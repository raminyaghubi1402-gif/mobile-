import unittest
from types import SimpleNamespace
from unittest.mock import patch
from lili_trader.market_analyst import analyze_market, _closed
from lili_trader.market_structure import StructureAnalysis, StructureEvent
from lili_trader.price_action_trend import TrendState
from lili_trader.support_resistance import PriceZone


def candles_for(direction="up", count=25):
    if direction == "up":
        centers = [10, 8, 12, 9, 14, 11, 16, 13, 18, 15, 20, 17, 22,
                   19, 24, 21, 26, 23, 28, 25, 30, 27, 32, 29, 34]
    elif direction == "down":
        centers = [34, 36, 32, 35, 30, 33, 28, 31, 26, 29, 24, 27, 22,
                   25, 20, 23, 18, 21, 16, 19, 14, 17, 12, 15, 10]
    else:
        # Last confirmed highs fall while lows rise: mixed/ranging structure.
        centers = [10, 8, 30, 9, 29, 10, 28, 11, 27, 12, 26, 13, 25,
                   14, 24, 15, 23, 16, 22, 17, 21, 18, 20, 19, 20]
    centers = centers[:count]
    return [
        {"timestamp": i, "open": center, "high": center + 0.3,
         "low": center - 0.3, "close": center}
        for i, center in enumerate(centers)
    ]


class ProfessionalMarketAnalystTests(unittest.TestCase):

    def test_closed_filter_rejects_false_or_ambiguous_closure_metadata(self):
        confirmed = {"timestamp": 1, "is_closed": True}
        open_candle = {"timestamp": 2, "is_closed": False}
        string_false = {"timestamp": 3, "is_closed": "false"}
        string_true = {"timestamp": 4, "is_closed": "true"}
        missing_flag = {"timestamp": 5}

        result = _closed(
            {"5m": [confirmed, open_candle, string_false, string_true, missing_flag]},
            "5m",
        )
        self.assertEqual(result, (confirmed, missing_flag))
        object_false = SimpleNamespace(timestamp=6, is_closed=False)
        object_string_true = SimpleNamespace(timestamp=7, is_closed="true")
        object_true = SimpleNamespace(timestamp=8, is_closed=True)
        object_without_flag = SimpleNamespace(timestamp=9)
        object_result = _closed(
            {"5m": [object_false, object_string_true, object_true, object_without_flag]},
            "5m",
        )
        self.assertEqual(object_result, (object_true, object_without_flag))

    def test_missing_required_timeframes_fails_closed(self):
        result = analyze_market("BTCIRT", {"1h": [], "15m": [], "5m": []})
        self.assertEqual(result.decision, "WAIT")
        self.assertIsNone(result.direction)
        self.assertIn("داده", result.report_fa)
        self.assertIn("هیچ معامله‌ای اجرا نمی‌شود", result.report_fa)

    def test_each_required_timeframe_fails_closed_when_closure_flag_is_false(self):
        for missing_tf in ("1h", "15m", "5m"):
            with self.subTest(timeframe=missing_tf):
                data = {}
                for timeframe in ("1h", "15m", "5m"):
                    rows = candles_for("up")
                    for row in rows:
                        row["is_closed"] = timeframe != missing_tf
                    data[timeframe] = rows
                result = analyze_market("BTCIRT", data, pivot_window=1)
                self.assertEqual(result.decision, "WAIT")
                self.assertIsNone(result.direction)
                self.assertIsNone(result.gate)

    def test_each_required_timeframe_fails_closed_on_invalid_ohlc(self):
        for invalid_tf in ("1h", "15m", "5m"):
            with self.subTest(timeframe=invalid_tf):
                data = {tf: candles_for("up") for tf in ("1h", "15m", "5m")}
                data[invalid_tf][-1]["low"] = data[invalid_tf][-1]["high"] + 1
                result = analyze_market("BTCIRT", data, pivot_window=1)
                self.assertEqual(result.decision, "WAIT")
                self.assertIsNone(result.direction)
                self.assertIsNone(result.gate)
                self.assertTrue(any(invalid_tf in warning for warning in result.report_fa.splitlines()))

    def test_rejects_empty_symbol(self):
        with self.assertRaises(ValueError):
            analyze_market(" ", {})

    def test_rejects_non_mapping_input(self):
        with self.assertRaises(TypeError):
            analyze_market("BTCIRT", [])

    def test_aligned_bullish_structure_without_zone_and_trigger_waits(self):
        up = candles_for("up")
        result = analyze_market("BTCIRT", {"1h": up, "15m": up, "5m": up}, pivot_window=1)
        self.assertEqual(result.decision, "WAIT")
        self.assertEqual(result.direction, "up")
        self.assertFalse(result.gate.allowed)
        self.assertEqual(result.gate.status, "wait_zone")

    def test_aligned_bearish_structure_without_zone_and_trigger_waits(self):
        down = candles_for("down")
        result = analyze_market("BTCIRT", {"1h": down, "15m": down, "5m": down}, pivot_window=1)
        self.assertEqual(result.decision, "WAIT")
        self.assertEqual(result.direction, "down")
        self.assertFalse(result.gate.allowed)
        self.assertEqual(result.gate.status, "wait_zone")

    def test_range_market_never_generates_entry(self):
        sideways = candles_for("range")
        result = analyze_market("BTCIRT", {"1h": sideways, "15m": sideways, "5m": sideways}, pivot_window=1)
        self.assertEqual(result.decision, "WAIT")
        self.assertFalse(result.gate.allowed)

    def test_opposite_five_minute_trigger_waits_instead_of_reversing(self):
        up, down = candles_for("up"), candles_for("down")
        result = analyze_market("BTCIRT", {"1h": up, "15m": up, "5m": down}, pivot_window=1)
        self.assertEqual(result.decision, "WAIT")
        self.assertEqual(result.direction, "up")
        self.assertIn(result.gate.status, ("wait_zone", "wait_trigger"))


    def test_market_analyst_allows_pullback_only_at_zone_with_five_minute_break(self):
        hourly = TrendState("up", "صعودی", 105.0, (120.0, 130.0),
                            (90.0, 100.0), 3, 3, "hourly up")
        fifteen = TrendState("down", "اصلاح نزولی", 105.0, (120.0, 115.0),
                             (110.0, 105.0), 3, 3, "15m pullback")
        five = TrendState("up", "بازگشت صعودی", 131.0, (120.0, 130.0),
                          (90.0, 95.0), 3, 3, "5m bullish break")
        support = PriceZone("support", 99.5, 100.5, 100.0, 3, 20, 2.5)
        hourly_rows = candles_for("up")
        fifteen_rows = candles_for("up")
        fifteen_rows[-1] = {"timestamp": 24, "open": 100.0, "high": 106.0,
                            "low": 99.0, "close": 105.0}
        five_rows = candles_for("up")
        five_rows[-2] = {"timestamp": 23, "open": 129.0, "high": 129.5,
                         "low": 128.5, "close": 129.0}
        five_rows[-1] = {"timestamp": 24, "open": 129.0, "high": 132.0,
                         "low": 128.8, "close": 131.0}
        # The strict analyst gate requires an independent BOS/CHoCH event
        # on the latest closed 5m candle, not merely mocked price-trend labels.
        fresh_bos = StructureEvent(
            "BOS_UP", 130.0, len(five_rows) - 1, 24, 18, 131.0,
            "شکست ساختار صعودی (BOS)"
        )
        hourly_structure = StructureAnalysis("up", None, (), (), 105.0, "hourly up")
        fifteen_structure = StructureAnalysis("down", None, (), (), 105.0, "15m pullback")
        five_structure = StructureAnalysis(
            "up", fresh_bos, (fresh_bos,), (), 131.0, "fresh 5m BOS"
        )
        with patch("lili_trader.market_analyst.classify_price_action_trend",
                   side_effect=(hourly, fifteen, five)):
            with patch("lili_trader.market_analyst.analyze_market_structure",
                       side_effect=(hourly_structure, fifteen_structure, five_structure)):
                with patch("lili_trader.market_analyst.find_support_resistance",
                           return_value=(support,)):
                    result = analyze_market(
                        "BTCIRT",
                        {"1h": hourly_rows, "15m": fifteen_rows, "5m": five_rows},
                        pivot_window=1,
                    )
        self.assertEqual(result.decision, "READY_BUY")
        self.assertTrue(result.gate.allowed)
        self.assertEqual(result.gate.direction, "up")
        self.assertIn("اصلاح ۱۵ دقیقه", result.gate.reason)

    def test_insufficient_hourly_history_never_becomes_ready(self):
        up = candles_for("up", count=12)
        result = analyze_market("BTCIRT", {"1h": up, "15m": up, "5m": up}, pivot_window=1)
        self.assertEqual(result.decision, "WAIT")
        self.assertEqual(result.regime.trend, "unknown")


    def test_hourly_choch_against_setup_blocks_entry(self):
        up = candles_for("up")
        event = StructureEvent(
            "CHOCH_DOWN", 100.0, 20, 20, 18, 99.0,
            "تغییر کاراکتر نزولی (CHoCH)"
        )
        bearish_structure = StructureAnalysis(
            "down", event, (event,), (), 99.0, "hourly CHoCH down"
        )
        with patch("lili_trader.market_analyst.analyze_market_structure",
                   return_value=bearish_structure):
            result = analyze_market(
                "BTCIRT", {"1h": up, "15m": up, "5m": up}, pivot_window=1
            )
        self.assertEqual(result.decision, "WAIT")
        self.assertFalse(result.gate.allowed)
        self.assertEqual(result.gate.status, "structure_conflict")
        self.assertTrue("مخالف" in result.gate.reason or "تعارض" in result.gate.reason)

    def test_unknown_hourly_or_mismatched_15m_structure_fails_closed(self):
        up = candles_for("up")
        hourly = TrendState("up", "صعودی", 105.0, (120.0, 130.0),
                            (90.0, 100.0), 3, 3, "hourly up")
        fifteen_down = TrendState("down", "اصلاح نزولی", 105.0, (120.0, 115.0),
                                  (110.0, 105.0), 3, 3, "15m pullback")
        five = TrendState("up", "بازگشت صعودی", 131.0, (120.0, 130.0),
                          (90.0, 95.0), 3, 3, "5m bullish")
        unknown_hour = StructureAnalysis("unknown", None, (), (), 105.0, "unknown")
        up_structure = StructureAnalysis("up", None, (), (), 105.0, "up")
        down_structure = StructureAnalysis("down", None, (), (), 105.0, "down")
        cases = (
            (unknown_hour, down_structure, up_structure),
            (up_structure, up_structure, up_structure),  # 15m trend is down, but structure says up
        )
        for structures in cases:
            with self.subTest(structures=structures):
                with patch("lili_trader.market_analyst.classify_price_action_trend",
                           side_effect=(hourly, fifteen_down, five)):
                    with patch("lili_trader.market_analyst.analyze_market_structure",
                               side_effect=structures):
                        with patch("lili_trader.market_analyst.find_support_resistance",
                                   return_value=()):
                            result = analyze_market(
                                "BTCIRT", {"1h": up, "15m": up, "5m": up},
                                pivot_window=1,
                            )
                self.assertEqual(result.decision, "WAIT")
                self.assertFalse(result.gate.allowed)
                self.assertEqual(result.gate.status, "structure_conflict")

    def test_market_structure_is_attached_to_each_timeframe_report(self):
        up = candles_for("up")
        result = analyze_market(
            "BTCIRT", {"1h": up, "15m": up, "5m": up}, pivot_window=1
        )
        frames = {frame.timeframe: frame for frame in result.frames}
        for timeframe in ("1h", "15m", "5m"):
            self.assertIsNotNone(frames[timeframe].structure)
        self.assertIn("ساختار مستقل", result.report_fa)


    def test_future_extension_does_not_rewrite_past_multitimeframe_structure(self):
        base = candles_for("up")
        future = [
            {"timestamp": 25 + i, "open": 40 + i, "high": 41 + i,
             "low": 39 + i, "close": 40.5 + i}
            for i in range(6)
        ]
        extended = base + future
        base_data = {tf: [dict(row) for row in base]
                     for tf in ("1h", "15m", "5m")}
        extended_data = {tf: [dict(row) for row in extended]
                         for tf in ("1h", "15m", "5m")}

        before = analyze_market("BTCIRT", base_data, pivot_window=1)
        after = analyze_market("BTCIRT", extended_data, pivot_window=1)
        before_frames = {frame.timeframe: frame for frame in before.frames}
        after_frames = {frame.timeframe: frame for frame in after.frames}

        for timeframe in ("1h", "15m", "5m"):
            with self.subTest(timeframe=timeframe):
                old_structure = before_frames[timeframe].structure
                new_structure = after_frames[timeframe].structure
                old_events = [
                    (event.kind, event.candle_index, event.level,
                     event.broken_swing_index, event.close)
                    for event in old_structure.events
                ]
                retained_events = [
                    (event.kind, event.candle_index, event.level,
                     event.broken_swing_index, event.close)
                    for event in new_structure.events
                    if event.candle_index < len(base)
                ]
                self.assertEqual(old_events, retained_events)

                old_swings = [
                    (swing.kind, swing.index, swing.price)
                    for swing in old_structure.swings
                ]
                retained_swings = [
                    (swing.kind, swing.index, swing.price)
                    for swing in new_structure.swings
                    if swing.index + 1 <= len(base) - 1
                ]
                self.assertEqual(old_swings, retained_swings)


if __name__ == "__main__":
    unittest.main()
