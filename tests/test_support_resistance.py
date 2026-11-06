import unittest

from lili_trader.support_resistance import find_support_resistance, nearest_zone


def candles_with_repeated_levels():
    rows = []
    for i in range(64):
        rows.append({
            "timestamp": i * 3600,
            "open": 100.0, "high": 102.0, "low": 98.0, "close": 100.0,
        })
    for i in (14, 30, 46):
        rows[i].update(high=110.0, close=101.0)
    for i in (20, 36, 52):
        rows[i].update(low=90.0, close=99.0)
    return rows


class SupportResistanceTests(unittest.TestCase):
    def test_finds_repeated_confirmed_resistance_and_support(self):
        zones = find_support_resistance(candles_with_repeated_levels())
        resistance = nearest_zone(zones, 100, "resistance")
        support = nearest_zone(zones, 100, "support")
        self.assertIsNotNone(resistance)
        self.assertIsNotNone(support)
        self.assertEqual(resistance.touches, 3)
        self.assertEqual(support.touches, 3)
        self.assertGreater(resistance.lower, support.upper)

    def test_unconfirmed_recent_pivot_is_not_used(self):
        rows = candles_with_repeated_levels()
        rows[-1].update(high=130.0, close=110.0)
        zones = find_support_resistance(rows)
        resistance = nearest_zone(zones, 110, "resistance")
        self.assertLess(resistance.midpoint, 120.0)

    def test_rejects_bad_ohlc_and_unsorted_timestamps(self):
        rows = candles_with_repeated_levels()
        rows[10]["low"] = 105.0
        with self.assertRaises(ValueError):
            find_support_resistance(rows)
        rows = candles_with_repeated_levels()
        rows[10]["timestamp"] = rows[9]["timestamp"]
        with self.assertRaises(ValueError):
            find_support_resistance(rows)

    def test_rejects_insufficient_data_and_invalid_parameters(self):
        with self.assertRaises(ValueError):
            find_support_resistance(candles_with_repeated_levels()[:10])
        with self.assertRaises(ValueError):
            find_support_resistance(candles_with_repeated_levels(), tolerance_atr=0)

    def test_nearest_zone_validates_arguments(self):
        zones = find_support_resistance(candles_with_repeated_levels())
        self.assertIsNotNone(nearest_zone(zones, 100, "support"))
        with self.assertRaises(ValueError):
            nearest_zone(zones, 0, "support")


if __name__ == "__main__":
    unittest.main()
