import json
import unittest
from urllib.parse import parse_qs, urlparse

from lili_trader.nobitex_data import NobitexAdapter


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


def payload(timestamps):
    count = len(timestamps)
    return {
        "s": "ok",
        "t": timestamps,
        "o": [100.0] * count,
        "h": [101.0] * count,
        "l": [99.0] * count,
        "c": [100.0] * count,
        "v": [1.0] * count,
    }


class NobitexPaginationTests(unittest.TestCase):
    def test_fetch_recent_sends_requested_page(self):
        seen = []

        def opener(request, timeout):
            query = parse_qs(urlparse(request.full_url).query)
            seen.append(query)
            return FakeResponse(payload([300]))

        candles = NobitexAdapter(opener=opener).fetch_recent(
            "BTCIRT", "5", 1, page=3
        )
        self.assertEqual(len(candles), 1)
        self.assertEqual(seen[0]["page"], ["3"])
        self.assertEqual(seen[0]["countback"], ["1"])

    def test_fetch_history_combines_pages_and_returns_latest_count(self):
        seen_pages = []

        def opener(request, timeout):
            query = parse_qs(urlparse(request.full_url).query)
            page = int(query["page"][0])
            seen_pages.append(page)
            if page == 1:
                return FakeResponse(payload(list(range(501, 1001))))
            return FakeResponse(payload(list(range(1, 501))))

        candles = NobitexAdapter(opener=opener).fetch_history(
            "BTCIRT", "5", 501
        )
        timestamps = [candle.timestamp for candle in candles]
        self.assertEqual(seen_pages, [1, 2])
        self.assertEqual(len(timestamps), 501)
        self.assertEqual(timestamps, list(range(500, 1001)))

    def test_fetch_history_continues_after_a_short_intermediate_page(self):
        seen_pages = []

        def opener(request, timeout):
            query = parse_qs(urlparse(request.full_url).query)
            page = int(query["page"][0])
            seen_pages.append(page)
            if page == 1:
                return FakeResponse(payload(list(range(1001, 1501))))
            if page == 2:
                return FakeResponse(payload(list(range(830, 1001))))
            return FakeResponse(payload(list(range(330, 830))))

        candles = NobitexAdapter(opener=opener).fetch_history(
            "BTCIRT", "15", 1500
        )
        self.assertEqual(seen_pages, [1, 2, 3])
        self.assertEqual(len(candles), 1171)
        self.assertEqual(candles[0].timestamp, 330)
        self.assertEqual(candles[-1].timestamp, 1500)

    def test_fetch_history_stops_when_older_page_has_no_data(self):
        seen_pages = []

        def opener(request, timeout):
            query = parse_qs(urlparse(request.full_url).query)
            page = int(query["page"][0])
            seen_pages.append(page)
            if page == 1:
                return FakeResponse(payload(list(range(501, 1001))))
            return FakeResponse({"s": "no_data"})

        candles = NobitexAdapter(opener=opener).fetch_history(
            "BTCIRT", "5", 1200
        )
        self.assertEqual(seen_pages, [1, 2])
        self.assertEqual(len(candles), 500)

    def test_fetch_history_rejects_more_than_supported_limit(self):
        with self.assertRaises(ValueError):
            NobitexAdapter().fetch_history("BTCIRT", "5", 5001)


if __name__ == "__main__":
    unittest.main()
