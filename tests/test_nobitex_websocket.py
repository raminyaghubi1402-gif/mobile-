import unittest
from lili_trader.nobitex_websocket import NobitexWebSocketError, channel_map, parse_candle_publication

class NobitexCandlePublicationTests(unittest.TestCase):
    def setUp(self):
        self.channel = "public:candle-BTCIRT-5"
        self.payload = {"t": 1_800_000_000, "o": 100.0, "h": 105.0,
                        "l": 99.0, "c": 103.0, "v": 2.5}

    def test_declares_six_requested_public_channels(self):
        channels = channel_map()
        self.assertEqual(len(channels), 6)
        self.assertIn("public:candle-ETHIRT-15", channels)
        self.assertIn("public:candle-BTCIRT-60", channels)

    def test_open_and_closed_updates_are_distinguished_by_exchange_time(self):
        opened = parse_candle_publication(self.channel, self.payload, received_at=1_800_000_100)
        closed = parse_candle_publication(self.channel, self.payload, received_at=1_800_000_300)
        self.assertFalse(opened["is_closed"])
        self.assertTrue(closed["is_closed"])

    def test_rejects_unknown_channel_and_missing_fields(self):
        with self.assertRaises(NobitexWebSocketError):
            parse_candle_publication("public:candle-BTCUSDT-5", self.payload, received_at=1_800_000_100)
        with self.assertRaises(NobitexWebSocketError):
            parse_candle_publication(self.channel, {"t": 1_800_000_000}, received_at=1_800_000_100)

    def test_rejects_boolean_fractional_and_misaligned_timestamps(self):
        for timestamp in (True, 1_800_000_000.5, 1_800_000_001):
            with self.subTest(timestamp=timestamp), self.assertRaises(NobitexWebSocketError):
                parse_candle_publication(self.channel, dict(self.payload, t=timestamp), received_at=1_800_000_100)

    def test_rejects_boolean_nonfinite_and_string_ohlcv(self):
        for value in (True, float("nan"), float("inf"), "100"):
            with self.subTest(value=value), self.assertRaises(NobitexWebSocketError):
                parse_candle_publication(self.channel, dict(self.payload, c=value), received_at=1_800_000_100)

    def test_rejects_invalid_ohlc_and_negative_volume(self):
        for payload in (dict(self.payload, h=102.0), dict(self.payload, l=104.0), dict(self.payload, v=-1.0)):
            with self.subTest(payload=payload), self.assertRaises(NobitexWebSocketError):
                parse_candle_publication(self.channel, payload, received_at=1_800_000_100)

    def test_rejects_future_candle_and_invalid_receive_time(self):
        with self.assertRaises(NobitexWebSocketError):
            parse_candle_publication(self.channel, dict(self.payload, t=1_800_000_300), received_at=1_800_000_100)
        with self.assertRaises(NobitexWebSocketError):
            parse_candle_publication(self.channel, self.payload, received_at=True)

if __name__ == "__main__":
    unittest.main()
