import json
import unittest

from lili_trader.market_data import BinanceSpotAdapter, MarketDataError


class FakeResponse:
    status = 200

    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class MarketDataTests(unittest.TestCase):
    def adapter(self, payload):
        return BinanceSpotAdapter(opener=lambda request, timeout: FakeResponse(payload))

    def test_parses_valid_candle(self):
        adapter = self.adapter([[1700000000000, "100", "110", "95", "105", "12"]])
        candle = adapter.fetch_latest("btcusdt", "1h")
        self.assertEqual(candle.symbol, "BTCUSDT")
        self.assertEqual(candle.close, 105.0)
        self.assertEqual(candle.source, "Binance Spot API")

    def test_rejects_bad_ohlc(self):
        adapter = self.adapter([[1700000000000, "100", "90", "95", "105", "12"]])
        with self.assertRaises(MarketDataError):
            adapter.fetch_latest("BTCUSDT")

    def test_rejects_empty_response(self):
        with self.assertRaises(MarketDataError):
            self.adapter([]).fetch_latest("BTCUSDT")

    def test_rejects_bad_symbol(self):
        with self.assertRaises(ValueError):
            self.adapter([]).fetch_latest("BTC-USDT")

    def test_forex_not_falsely_claimed_live(self):
        from lili_trader.market_data import ForexDataAdapter
        with self.assertRaises(MarketDataError):
            ForexDataAdapter().fetch_latest("EURUSD")


if __name__ == "__main__":
    unittest.main()
