import json
import tempfile
import unittest
from pathlib import Path

from lili_trader.nobitex_websocket import (
    NobitexCandleWebSocket,
    NobitexWebSocketError,
    channel_map,
    parse_candle_publication,
)


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def monotonic(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class FakeWebSocketApp:
    def __init__(
        self,
        url,
        *,
        on_open,
        on_message,
        on_error,
        on_close,
        clock,
        published_channels,
        failed_channels=(),
        connect_error=False,
    ):
        self.url = url
        self.on_open = on_open
        self.on_message = on_message
        self.on_error = on_error
        self.on_close = on_close
        self.clock = clock
        self.published_channels = set(published_channels)
        self.failed_channels = set(failed_channels)
        self.connect_error = connect_error
        self.sent = []
        self.closed = False

    def send(self, message):
        self.sent.append(message)

    def close(self):
        self.closed = True

    def run_forever(self, **_kwargs):
        self.on_open(self)
        if self.connect_error:
            self.on_message(
                self,
                json.dumps({"id": 1, "error": {"code": 109, "message": "denied"}}),
            )
            self.clock.advance(2)
            return

        self.on_message(self, json.dumps({"id": 1, "connect": {}}))
        requests = [
            json.loads(message)
            for message in self.sent
            if "subscribe" in json.loads(message)
        ]
        timestamp = 1_800_000_000
        payload = {
            "t": timestamp,
            "o": 100.0,
            "h": 105.0,
            "l": 99.0,
            "c": 103.0,
            "v": 2.5,
        }
        for request in requests:
            channel = request["subscribe"]["channel"]
            if channel in self.failed_channels:
                self.on_message(
                    self,
                    json.dumps({
                        "id": request["id"],
                        "error": {"code": 109, "message": "denied"},
                    }),
                )
                continue
            self.on_message(
                self,
                json.dumps({"id": request["id"], "subscribe": {}}),
            )
            if channel in self.published_channels:
                self.on_message(
                    self,
                    json.dumps({
                        "push": {
                            "channel": channel,
                            "pub": {"data": json.dumps(payload)},
                        }
                    }),
                )
        self.on_message(self, "{}")
        self.clock.advance(2)


class NobitexCandlePublicationTests(unittest.TestCase):
    def setUp(self):
        self.channel = "public:candle-BTCIRT-5"
        self.payload = {
            "t": 1_800_000_000,
            "o": 100.0,
            "h": 105.0,
            "l": 99.0,
            "c": 103.0,
            "v": 2.5,
        }

    def test_declares_six_requested_public_channels(self):
        channels = channel_map()
        self.assertEqual(len(channels), 6)
        self.assertIn("public:candle-ETHIRT-15", channels)
        self.assertIn("public:candle-BTCIRT-60", channels)

    def test_open_and_closed_updates_are_distinguished_by_exchange_time(self):
        opened = parse_candle_publication(
            self.channel, self.payload, received_at=1_800_000_100
        )
        closed = parse_candle_publication(
            self.channel, self.payload, received_at=1_800_000_300
        )
        self.assertFalse(opened["is_closed"])
        self.assertTrue(closed["is_closed"])

    def test_rejects_unknown_channel_and_missing_fields(self):
        with self.assertRaises(NobitexWebSocketError):
            parse_candle_publication(
                "public:candle-BTCUSDT-5",
                self.payload,
                received_at=1_800_000_100,
            )
        with self.assertRaises(NobitexWebSocketError):
            parse_candle_publication(
                self.channel, {"t": 1_800_000_000}, received_at=1_800_000_100
            )

    def test_rejects_boolean_fractional_and_misaligned_timestamps(self):
        for timestamp in (True, 1_800_000_000.5, 1_800_000_001):
            with self.subTest(timestamp=timestamp), self.assertRaises(
                NobitexWebSocketError
            ):
                parse_candle_publication(
                    self.channel,
                    dict(self.payload, t=timestamp),
                    received_at=1_800_000_100,
                )

    def test_rejects_boolean_nonfinite_and_string_ohlcv(self):
        for value in (True, float("nan"), float("inf"), "100"):
            with self.subTest(value=value), self.assertRaises(NobitexWebSocketError):
                parse_candle_publication(
                    self.channel,
                    dict(self.payload, c=value),
                    received_at=1_800_000_100,
                )

    def test_rejects_invalid_ohlc_and_negative_volume(self):
        invalid_payloads = (
            dict(self.payload, h=102.0),
            dict(self.payload, l=104.0),
            dict(self.payload, v=-1.0),
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload), self.assertRaises(
                NobitexWebSocketError
            ):
                parse_candle_publication(
                    self.channel, payload, received_at=1_800_000_100
                )

    def test_rejects_future_candle_and_invalid_receive_time(self):
        with self.assertRaises(NobitexWebSocketError):
            parse_candle_publication(
                self.channel,
                dict(self.payload, t=1_800_000_300),
                received_at=1_800_000_100,
            )
        with self.assertRaises(NobitexWebSocketError):
            parse_candle_publication(
                self.channel, self.payload, received_at=True
            )


class NobitexCandleStreamProtocolTests(unittest.TestCase):
    def run_fake_stream(
        self, *, published_channels=None, failed_channels=(), connect_error=False
    ):
        clock = FakeClock()
        channels = list(channel_map())
        if published_channels is None:
            published_channels = channels
        apps = []

        def factory(url, **callbacks):
            app = FakeWebSocketApp(
                url,
                **callbacks,
                clock=clock,
                published_channels=published_channels,
                failed_channels=failed_channels,
                connect_error=connect_error,
            )
            apps.append(app)
            return app

        stream = NobitexCandleWebSocket(
            websocket_app_factory=factory,
            wall_clock=lambda: 1_800_000_100,
            monotonic=clock.monotonic,
            sleep=lambda seconds: clock.advance(seconds),
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "sample.jsonl"
            result = stream.stream(duration_seconds=1, output_path=output)
            records = (
                [
                    json.loads(line)
                    for line in output.read_text(encoding="utf-8").splitlines()
                ]
                if output.exists()
                else []
            )
        return result, records, apps

    def test_success_requires_ack_and_publication_on_all_six_channels(self):
        result, records, apps = self.run_fake_stream()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["subscribed_channels"]), 6)
        self.assertEqual(result["missing_subscriptions"], [])
        self.assertEqual(result["missing_publications"], [])
        self.assertEqual(result["total_publications"], 6)
        self.assertEqual(len(records), 6)
        self.assertIn("{}", apps[0].sent)
        self.assertTrue(apps[0].closed)

    def test_missing_one_channel_publication_is_incomplete_not_success(self):
        missing = "public:candle-ETHIRT-60"
        result, records, _apps = self.run_fake_stream(
            published_channels=set(channel_map()) - {missing}
        )
        self.assertEqual(result["status"], "incomplete_channels")
        self.assertEqual(result["missing_publications"], [missing])
        self.assertEqual(result["missing_subscriptions"], [])
        self.assertEqual(len(records), 5)

    def test_subscription_error_is_reported_for_the_exact_channel(self):
        failed = "public:candle-BTCIRT-60"
        result, records, _apps = self.run_fake_stream(failed_channels={failed})
        self.assertEqual(result["status"], "incomplete_channels")
        self.assertEqual(result["missing_subscriptions"], [failed])
        self.assertEqual(result["subscription_errors"], {failed: "109"})
        self.assertNotIn(failed, result["subscribed_channels"])
        self.assertEqual(len(records), 5)

    def test_connect_error_cannot_be_reported_as_success(self):
        result, records, _apps = self.run_fake_stream(connect_error=True)
        self.assertEqual(result["status"], "no_publications")
        self.assertEqual(result["total_publications"], 0)
        self.assertEqual(len(result["missing_subscriptions"]), 6)
        self.assertEqual(len(records), 0)
        self.assertIn("connect_error", result["errors"])


if __name__ == "__main__":
    unittest.main()
