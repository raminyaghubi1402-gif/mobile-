import json
import unittest
from io import BytesIO
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

from lili_trader.nobitex_data import NobitexAdapter, NobitexDataError


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
        seen_to = []

        def opener(request, timeout):
            query = parse_qs(urlparse(request.full_url).query)
            page = int(query["page"][0])
            seen_pages.append(page)
            seen_to.append(query["to"][0])
            if page == 1:
                return FakeResponse(payload(list(range(501, 1001))))
            return FakeResponse(payload(list(range(1, 501))))

        candles = NobitexAdapter(opener=opener).fetch_history(
            "BTCIRT", "5", 501
        )
        timestamps = [candle.timestamp for candle in candles]
        self.assertEqual(seen_pages, [1, 2])
        self.assertEqual(len(set(seen_to)), 1, "all pages must share one snapshot timestamp")
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

    def test_fetch_history_stops_when_page_cursor_repeats_same_data(self):
        seen_pages = []

        def opener(request, timeout):
            query = parse_qs(urlparse(request.full_url).query)
            seen_pages.append(int(query["page"][0]))
            return FakeResponse(payload(list(range(501, 1001))))

        candles = NobitexAdapter(opener=opener).fetch_history(
            "BTCIRT", "5", 1500
        )
        self.assertEqual(seen_pages, [1, 2])
        self.assertEqual(len(candles), 500)

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

    def test_fetch_recent_rejects_nonpositive_or_fractional_timestamps(self):
        for bad_timestamp in (0, -1, 300.5, True):
            with self.subTest(timestamp=bad_timestamp):
                def opener(request, timeout):
                    return FakeResponse(payload([bad_timestamp]))

                with self.assertRaises(NobitexDataError):
                    NobitexAdapter(opener=opener).fetch_recent("BTCIRT", "5", 1)

    def test_fetch_recent_rejects_boolean_ohlcv_values(self):
        for field in ("o", "h", "l", "c", "v"):
            with self.subTest(field=field):
                invalid = payload([300])
                invalid[field] = [True]

                def opener(request, timeout):
                    return FakeResponse(invalid)

                with self.assertRaises(NobitexDataError):
                    NobitexAdapter(opener=opener).fetch_recent("BTCIRT", "5", 1)

    def test_fetch_recent_rejects_zero_price_but_allows_zero_volume(self):
        invalid = payload([300])
        invalid["l"] = [0]

        def invalid_opener(request, timeout):
            return FakeResponse(invalid)

        with self.assertRaises(NobitexDataError):
            NobitexAdapter(opener=invalid_opener).fetch_recent("BTCIRT", "5", 1)

        zero_volume = payload([300])
        zero_volume["v"] = [0]

        def valid_opener(request, timeout):
            return FakeResponse(zero_volume)

        candle = NobitexAdapter(opener=valid_opener).fetch_recent("BTCIRT", "5", 1)[0]
        self.assertEqual(candle.volume, 0)

    def test_fetch_history_accepts_expanded_backfill_limit(self):
        seen_pages = []

        def opener(request, timeout):
            query = parse_qs(urlparse(request.full_url).query)
            page = int(query["page"][0])
            seen_pages.append(page)
            start = 10_000 - page * 500
            return FakeResponse(payload(list(range(start, start + 500))))

        candles = NobitexAdapter(opener=opener).fetch_history("BTCIRT", "1", 5001)
        self.assertEqual(seen_pages, list(range(1, 12)))
        self.assertEqual(len(candles), 5001)

    def test_fetch_history_rejects_above_expanded_limit(self):
        with self.assertRaises(ValueError):
            NobitexAdapter().fetch_history("BTCIRT", "5", 50001)


    def test_fetch_recent_sends_exact_range_without_countback(self):
        seen = []

        def opener(request, timeout):
            query = parse_qs(urlparse(request.full_url).query)
            seen.append(query)
            return FakeResponse(payload([900]))

        candles = NobitexAdapter(opener=opener).fetch_recent(
            "BTCIRT", "5", 500, from_timestamp=900, to_timestamp=1199, page=1
        )
        self.assertEqual([c.timestamp for c in candles], [900])
        self.assertEqual(seen[0]["from"], ["900"])
        self.assertEqual(seen[0]["to"], ["1199"])
        self.assertEqual(seen[0]["page"], ["1"])
        self.assertNotIn("countback", seen[0])

    def test_fetch_recent_rejects_invalid_or_oversized_ranges(self):
        adapter = NobitexAdapter(opener=lambda request, timeout: FakeResponse(payload([])))
        for kwargs in (
            {"from_timestamp": True, "to_timestamp": 900},
            {"from_timestamp": 1200, "to_timestamp": 900},
            {"from_timestamp": 900, "to_timestamp": 900 + 500 * 300},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    adapter.fetch_recent("BTCIRT", "5", 500, **kwargs)


class NobitexRetryTests(unittest.TestCase):
    def test_retries_timeout_once_then_returns_data(self):
        calls = []
        delays = []

        def opener(request, timeout):
            calls.append(request.full_url)
            if len(calls) == 1:
                raise TimeoutError("temporary network timeout")
            return FakeResponse(payload([300]))

        candles = NobitexAdapter(opener=opener, sleep=delays.append).fetch_recent(
            "BTCIRT", "5", 1
        )
        self.assertEqual(len(candles), 1)
        self.assertEqual(len(calls), 2)
        self.assertEqual(delays, [0.5])

    def test_retries_transient_http_status_with_bounded_retry_after(self):
        calls = []
        delays = []

        def opener(request, timeout):
            calls.append(request.full_url)
            if len(calls) == 1:
                raise HTTPError(
                    request.full_url, 503, "temporarily unavailable",
                    {"Retry-After": "10"}, BytesIO()
                )
            return FakeResponse(payload([300]))

        candles = NobitexAdapter(opener=opener, sleep=delays.append).fetch_recent(
            "BTCIRT", "5", 1
        )
        self.assertEqual(len(candles), 1)
        self.assertEqual(len(calls), 2)
        self.assertEqual(delays, [2.0], "server retry hints must be bounded")

    def test_exhausted_retries_fail_closed_with_sanitized_error(self):
        calls = []
        delays = []

        def opener(request, timeout):
            calls.append(request.full_url)
            raise TimeoutError("do not expose this transport detail")

        adapter = NobitexAdapter(
            opener=opener, sleep=delays.append, max_retries=2
        )
        with self.assertRaises(NobitexDataError) as caught:
            adapter.fetch_recent("BTCIRT", "5", 1)
        self.assertEqual(len(calls), 3)
        self.assertEqual(delays, [0.5, 1.0])
        self.assertNotIn("transport detail", str(caught.exception))

    def test_non_retryable_http_error_fails_without_waiting(self):
        calls = []
        delays = []

        def opener(request, timeout):
            calls.append(request.full_url)
            raise HTTPError(request.full_url, 400, "bad request", {}, BytesIO())

        adapter = NobitexAdapter(opener=opener, sleep=delays.append)
        with self.assertRaises(NobitexDataError):
            adapter.fetch_recent("BTCIRT", "5", 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(delays, [])

    def test_retry_configuration_rejects_boolean_and_negative_values(self):
        for invalid in (True, -1, 1.5, "2"):
            with self.subTest(max_retries=invalid):
                with self.assertRaises(ValueError):
                    NobitexAdapter(max_retries=invalid)


if __name__ == "__main__":
    unittest.main()
