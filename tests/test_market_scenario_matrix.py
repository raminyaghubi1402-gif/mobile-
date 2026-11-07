"""Scenario matrix: regime policy and end-to-end analyst fail-closed behavior."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from lili_trader.market_analyst import analyze_market
from lili_trader.market_regime import classify_market_regime, select_strategy_candidates
from lili_trader.market_structure import StructureAnalysis, StructureEvent
from lili_trader.price_action_trend import TrendState
from lili_trader.support_resistance import PriceZone


def candles(direction="up", count=30):
    if direction == "up":
        closes = [100 + i * 2 for i in range(count)]
    elif direction == "down":
        closes = [160 - i * 2 for i in range(count)]
    else:
        closes = [100 + (i % 2) * 2 for i in range(count)]
    return [
        {"timestamp": i, "open": c - .2, "high": c + .5,
         "low": c - .5, "close": c}
        for i, c in enumerate(closes)
    ]


def trend(direction, highs=(120., 130.), lows=(90., 100.), close=105.):
    return TrendState(direction, direction, close, highs, lows, 3, 3, "scenario")


def structure(direction, event=None):
    return StructureAnalysis(direction, event, (event,) if event else (), (), 105., "scenario")


class MarketRegimeScenarioMatrix(unittest.TestCase):
    def test_regime_to_strategy_matrix_never_selects_countertrend(self):
        cases = (
            ("up", "up", {"buy"}, False),
            ("down", "down", {"sell"}, False),
            ("range", "range", set(), True),
            ("unknown", "unknown", set(), True),
        )
        for candle_direction, trend_direction, expected_tokens, wait_only in cases:
            with self.subTest(candle_direction=candle_direction):
                rows = candles(candle_direction)
                state = trend(trend_direction)
                regime = classify_market_regime(rows, state)
                candidates = select_strategy_candidates(regime)
                names = {item.name for item in candidates}
                if wait_only:
                    self.assertTrue(names)
                    self.assertTrue(all("no_trade" in name for name in names))
                elif expected_tokens == {"buy"}:
                    self.assertTrue(names)
                    self.assertTrue(all("buy" in name for name in names))
                    self.assertFalse(any("sell" in name for name in names))
                else:
                    self.assertTrue(names)
                    self.assertTrue(all("sell" in name for name in names))
                    self.assertFalse(any("buy" in name for name in names))

    def test_high_volatility_overrides_trend_and_forces_wait(self):
        rows = candles("up")
        for row in rows[-5:]:
            c = row["close"]
            row.update(open=c-15, high=c+30, low=c-30)
        regime = classify_market_regime(rows, trend("up"))
        self.assertEqual(regime.volatility, "high")
        self.assertEqual([x.name for x in select_strategy_candidates(regime)],
                         ["no_trade_wait"])

    def test_insufficient_history_cannot_create_strategy(self):
        regime = classify_market_regime(candles(count=8), trend("up"))
        self.assertEqual(regime.trend, "unknown")
        self.assertEqual(regime.allowed_strategies, ())
        self.assertEqual([x.name for x in select_strategy_candidates(regime)],
                         ["no_trade_wait"])


class EndToEndDecisionScenarioMatrix(unittest.TestCase):
    def setUp(self):
        self.up = trend("up")
        self.down_pullback = trend("down", highs=(130., 125.), lows=(110., 105.))
        self.down = trend("down", highs=(110., 100.), lows=(90., 80.))
        self.five_up = trend("up")
        self.five_down = trend("down", highs=(110., 100.), lows=(90., 80.))
        self.support = PriceZone("support", 99.5, 100.5, 100., 3, 20, 2.5)
        self.resistance = PriceZone("resistance", 109.5, 110.5, 110., 3, 20, 2.5)
        self.rows = {"1h": candles("up"), "15m": candles("up"), "5m": candles("up")}

    def run_case(self, hourly, fifteen, five, structures, zone, regime_trend,
                 gate_trigger=True, gate_zone=True):
        fresh_event = StructureEvent(
            "BOS_UP" if gate_trigger else "BOS_DOWN", 130., 29, 29, 27, 131.,
            "fresh break"
        )
        five_structure = structure(five.direction, fresh_event)
        with patch("lili_trader.market_analyst.classify_price_action_trend",
                   side_effect=(hourly, fifteen, five)):
            with patch("lili_trader.market_analyst.analyze_market_structure",
                       side_effect=(*structures, five_structure)):
                with patch("lili_trader.market_analyst.find_support_resistance",
                           side_effect=(zone, zone, zone)):
                    regime = SimpleNamespace(
                        trend=regime_trend, volatility="normal",
                        confidence="moderate", reason="scenario"
                    )
                    candidates = ()
                    with patch("lili_trader.market_analyst.classify_market_regime",
                               return_value=regime):
                        with patch("lili_trader.market_analyst.select_strategy_candidates",
                                   return_value=candidates):
                            # Adjust latest 5m close so close-cross gate agrees with test.
                            rows = {k: list(v) for k, v in self.rows.items()}
                            rows["5m"][-2] = dict(rows["5m"][-2], close=129.)
                            rows["5m"][-1] = dict(rows["5m"][-1], close=131. if gate_trigger else 129.)
                            rows["15m"][-1] = dict(rows["15m"][-1], low=99., high=106., close=105.)
                            return analyze_market(
                                "TEST", rows, pivot_window=1
                            )

    def test_bull_trend_pullback_confirmed_yields_buy_only(self):
        result = self.run_case(
            self.up, self.down_pullback, self.five_up,
            (structure("up"), structure("down")), (self.support,), "up"
        )
        self.assertEqual(result.decision, "READY_BUY")
        self.assertEqual(result.direction, "up")

    def test_bear_trend_pullback_confirmed_yields_sell_only(self):
        # This fixture verifies direction policy at the gate/regime layer;
        # a fully bearish price series and a resistance-touch setup are used.
        down_rows = {"1h": candles("down"), "15m": candles("down"), "5m": candles("down")}
        bearish_hour = trend("down", highs=(130., 120.), lows=(90., 80.), close=85.)
        bearish_fifteen = trend("up", highs=(110., 115.), lows=(90., 95.), close=85.)
        bearish_five = trend("down", highs=(110., 100.), lows=(90., 80.), close=79.)
        event = StructureEvent("BOS_DOWN", 80., 29, 29, 27, 79., "fresh bearish break")
        with patch("lili_trader.market_analyst.classify_price_action_trend",
                   side_effect=(bearish_hour, bearish_fifteen, bearish_five)):
            with patch("lili_trader.market_analyst.analyze_market_structure",
                       side_effect=(structure("down"), structure("up"),
                                   structure("down", event))):
                with patch("lili_trader.market_analyst.find_support_resistance",
                           return_value=(self.resistance,)):
                    regime = SimpleNamespace(trend="down", volatility="normal",
                                            confidence="moderate", reason="scenario")
                    with patch("lili_trader.market_analyst.classify_market_regime",
                               return_value=regime):
                        with patch("lili_trader.market_analyst.select_strategy_candidates",
                                   return_value=()):
                            # 15m range intersects resistance zone (109.5–110.5).
                            down_rows["15m"][-1] = dict(down_rows["15m"][-1],
                                                        low=109., high=112., close=111.)
                            down_rows["5m"][-2] = dict(down_rows["5m"][-2], close=81.)
                            down_rows["5m"][-1] = dict(down_rows["5m"][-1], close=79.)
                            result = analyze_market("TEST", down_rows, pivot_window=1)
        self.assertEqual(result.decision, "READY_SELL")
        self.assertEqual(result.direction, "down")

    def test_range_or_unknown_regime_never_becomes_ready(self):
        result = self.run_case(
            self.up, self.down_pullback, self.five_up,
            (structure("up"), structure("down")), (self.support,), "range"
        )
        self.assertEqual(result.decision, "WAIT")

    def test_false_break_wrong_5m_event_never_becomes_ready(self):
        result = self.run_case(
            self.up, self.down_pullback, self.five_up,
            (structure("up"), structure("down")), (self.support,), "up",
            gate_trigger=False
        )
        self.assertEqual(result.decision, "WAIT")

    def test_hourly_choch_reversal_blocks_old_direction_setup(self):
        choch = StructureEvent("CHOCH_DOWN", 100., 20, 20, 18, 99.,
                               "hourly reversal")
        result = self.run_case(
            self.up, self.down_pullback, self.five_up,
            (structure("up", choch), structure("down")), (self.support,), "up"
        )
        self.assertEqual(result.decision, "WAIT")
        self.assertEqual(result.gate.status, "structure_conflict")


if __name__ == "__main__":
    unittest.main()
