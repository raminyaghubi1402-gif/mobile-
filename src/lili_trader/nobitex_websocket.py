"""Read-only Nobitex public candle stream over Centrifugo WebSocket.

Stores every exchange publication as an immutable JSONL observation. Repeated
updates for an open candle are expected; they are not separate closed candles.
No account token, private channel, wallet, or order API is used.
"""
from __future__ import annotations

import json
import math
import threading
import time
from pathlib import Path
from typing import Any, Callable

WEBSOCKET_URL = "wss://ws.nobitex.ir/connection/websocket"
MARKETS = ("BTCIRT", "ETHIRT")
RESOLUTIONS = {"5": 300, "15": 900, "60": 3600}


class NobitexWebSocketError(RuntimeError):
    """Raised when the public candle stream cannot be safely processed."""


def channel_map() -> dict[str, tuple[str, str]]:
    return {
        f"public:candle-{symbol}-{resolution}": (symbol, resolution)
        for symbol in MARKETS
        for resolution in RESOLUTIONS
    }


def parse_candle_publication(
    channel: str, payload: Any, *, received_at: int
) -> dict:
    """Validate one documented Nobitex candle publication without coercion."""
    channels = channel_map()
    if channel not in channels:
        raise NobitexWebSocketError("unexpected candle channel")
    symbol, resolution = channels[channel]
    if not isinstance(payload, dict) or any(
        key not in payload for key in ("t", "o", "h", "l", "c", "v")
    ):
        raise NobitexWebSocketError("candle payload is missing required fields")

    raw_timestamp = payload["t"]
    if isinstance(raw_timestamp, bool) or not isinstance(raw_timestamp, (int, float)):
        raise NobitexWebSocketError("candle timestamp must be numeric")
    if (
        not math.isfinite(float(raw_timestamp))
        or not float(raw_timestamp).is_integer()
    ):
        raise NobitexWebSocketError("candle timestamp must be a finite integer")
    timestamp = int(raw_timestamp)
    if timestamp <= 0 or timestamp % RESOLUTIONS[resolution] != 0:
        raise NobitexWebSocketError("candle timestamp is invalid or misaligned")
    if type(received_at) is not int or received_at <= 0:
        raise NobitexWebSocketError("received_at must be a positive integer")
    if timestamp > received_at + 5:
        raise NobitexWebSocketError("candle timestamp is in the future")

    values = []
    for key in ("o", "h", "l", "c", "v"):
        value = payload[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise NobitexWebSocketError(f"candle {key} must be numeric")
        number = float(value)
        if not math.isfinite(number):
            raise NobitexWebSocketError(f"candle {key} must be finite")
        values.append(number)

    open_price, high, low, close, volume = values
    if min(open_price, high, low, close) <= 0 or volume < 0:
        raise NobitexWebSocketError("candle prices or volume are outside valid bounds")
    if high < max(open_price, close) or low > min(open_price, close) or high < low:
        raise NobitexWebSocketError("candle OHLC relationship is invalid")

    seconds = RESOLUTIONS[resolution]
    return {
        "source": "Nobitex public WebSocket",
        "symbol": symbol,
        "resolution": resolution,
        "timestamp": timestamp,
        "received_at": received_at,
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "is_closed": received_at >= timestamp + seconds,
    }


class NobitexCandleWebSocket:
    """Bounded, reconnecting reader for the six public candle channels."""

    def __init__(
        self,
        *,
        websocket_app_factory: Callable | None = None,
        wall_clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.websocket_app_factory = websocket_app_factory
        self.wall_clock = wall_clock
        self.monotonic = monotonic
        self.sleep = sleep
        self.counts = {channel: 0 for channel in channel_map()}
        self.errors: list[str] = []
        self.subscribed_channels: set[str] = set()
        self.subscription_errors: dict[str, str] = {}

    def stream(self, *, duration_seconds: int, output_path: str | Path) -> dict:
        if type(duration_seconds) is not int or not 1 <= duration_seconds <= 3600:
            raise ValueError("duration_seconds must be an integer from 1 to 3600")

        factory = self.websocket_app_factory
        if factory is None:
            try:
                import websocket

                factory = websocket.WebSocketApp
            except ImportError as exc:
                raise NobitexWebSocketError(
                    "install websocket-client with: pip install websocket-client"
                ) from exc

        channels = channel_map()
        self.counts = {channel: 0 for channel in channels}
        self.errors = []
        self.subscribed_channels = set()
        self.subscription_errors = {}

        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        deadline = self.monotonic() + duration_seconds
        delay = 1.0

        with path.open("a", encoding="utf-8") as output:
            while self.monotonic() < deadline:
                next_id = {"value": 2}
                request_channels: dict[int, str] = {}

                def on_open(app):
                    app.send(
                        json.dumps(
                            {"connect": {}, "id": 1},
                            separators=(",", ":"),
                        )
                    )

                def on_message(app, raw):
                    if raw == "{}":
                        app.send("{}")
                        return
                    try:
                        frame = json.loads(raw)
                    except (TypeError, json.JSONDecodeError):
                        self.errors.append("invalid_json_frame")
                        return
                    if not isinstance(frame, dict):
                        self.errors.append("invalid_frame_shape")
                        return

                    if frame.get("id") == 1:
                        if "error" in frame:
                            self.errors.append("connect_error")
                            app.close()
                            return
                        if "connect" in frame:
                            for channel in channels:
                                request_id = next_id["value"]
                                next_id["value"] += 1
                                request_channels[request_id] = channel
                                app.send(
                                    json.dumps(
                                        {
                                            "id": request_id,
                                            "subscribe": {"channel": channel},
                                        },
                                        separators=(",", ":"),
                                    )
                                )
                            return

                    request_id = frame.get("id")
                    if request_id in request_channels:
                        channel = request_channels.pop(request_id)
                        if "error" in frame:
                            error = frame.get("error")
                            code = (
                                error.get("code")
                                if isinstance(error, dict)
                                else None
                            )
                            error_code = str(code) if code is not None else "unknown"
                            self.subscription_errors[channel] = error_code
                            self.errors.append(
                                f"subscription_error:{channel}:{error_code}"
                            )
                        elif isinstance(frame.get("subscribe"), dict):
                            self.subscribed_channels.add(channel)
                            self.subscription_errors.pop(channel, None)
                        else:
                            self.subscription_errors[channel] = "invalid_ack"
                            self.errors.append(
                                f"subscription_ack_invalid:{channel}"
                            )
                        return

                    push = frame.get("push")
                    if not isinstance(push, dict):
                        return
                    channel = push.get("channel")
                    publication = push.get("pub")
                    if channel not in channels or not isinstance(publication, dict):
                        return
                    data = publication.get("data")
                    if isinstance(data, str):
                        try:
                            data = json.loads(data)
                        except json.JSONDecodeError:
                            self.errors.append("invalid_candle_json")
                            return
                    try:
                        record = parse_candle_publication(
                            channel,
                            data,
                            received_at=int(self.wall_clock()),
                        )
                    except NobitexWebSocketError as exc:
                        self.errors.append(str(exc))
                        return

                    output.write(json.dumps(record, sort_keys=True) + "\n")
                    output.flush()
                    self.counts[channel] += 1

                def on_error(_app, error):
                    self.errors.append(f"transport_error:{type(error).__name__}")

                app = factory(
                    WEBSOCKET_URL,
                    on_open=on_open,
                    on_message=on_message,
                    on_error=on_error,
                    on_close=lambda *_: None,
                )
                timer = threading.Timer(
                    max(0.1, deadline - self.monotonic()), app.close
                )
                timer.daemon = True
                timer.start()
                try:
                    app.run_forever(ping_interval=20, ping_timeout=10)
                except Exception as exc:
                    self.errors.append(f"transport_error:{type(exc).__name__}")
                finally:
                    timer.cancel()
                    try:
                        app.close()
                    except Exception:
                        pass

                if self.monotonic() >= deadline:
                    break
                self.sleep(min(delay, max(0.0, deadline - self.monotonic())))
                delay = min(delay * 2, 30.0)

        total = sum(self.counts.values())
        missing_subscriptions = [
            channel for channel in channels if channel not in self.subscribed_channels
        ]
        missing_publications = [
            channel for channel, count in self.counts.items() if count == 0
        ]
        if total == 0:
            status = "no_publications"
        elif missing_subscriptions or missing_publications:
            status = "incomplete_channels"
        else:
            status = "ok"

        return {
            "status": status,
            "url": WEBSOCKET_URL,
            "duration_seconds": duration_seconds,
            "subscribed_channels": [
                channel for channel in channels if channel in self.subscribed_channels
            ],
            "missing_subscriptions": missing_subscriptions,
            "missing_publications": missing_publications,
            "subscription_errors": dict(self.subscription_errors),
            "publications_by_channel": dict(self.counts),
            "total_publications": total,
            "errors": list(self.errors),
            "output_path": str(path),
        }
