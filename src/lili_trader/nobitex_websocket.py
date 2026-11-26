"""Read-only Nobitex public candle stream over Centrifugo WebSocket.

Stores every exchange publication as an immutable JSONL observation. Repeated
updates for an open candle are expected; they are not separate closed candles.
No account token, private channel, wallet, or order API is used.
"""
from __future__ import annotations
import json, math, threading, time
from pathlib import Path
from typing import Any, Callable

WEBSOCKET_URL = "wss://ws.nobitex.ir/connection/websocket"
MARKETS = ("BTCIRT", "ETHIRT")
RESOLUTIONS = {"5": 300, "15": 900, "60": 3600}

class NobitexWebSocketError(RuntimeError):
    """Raised when the public candle stream cannot be safely processed."""

def channel_map() -> dict[str, tuple[str, str]]:
    return {f"public:candle-{symbol}-{resolution}": (symbol, resolution)
            for symbol in MARKETS for resolution in RESOLUTIONS}

def parse_candle_publication(channel: str, payload: Any, *, received_at: int) -> dict:
    """Validate one documented Nobitex candle publication without coercion."""
    channels = channel_map()
    if channel not in channels:
        raise NobitexWebSocketError("unexpected candle channel")
    symbol, resolution = channels[channel]
    if not isinstance(payload, dict) or any(k not in payload for k in ("t","o","h","l","c","v")):
        raise NobitexWebSocketError("candle payload is missing required fields")
    raw_t = payload["t"]
    if isinstance(raw_t, bool) or not isinstance(raw_t, (int, float)):
        raise NobitexWebSocketError("candle timestamp must be numeric")
    if not math.isfinite(float(raw_t)) or not float(raw_t).is_integer():
        raise NobitexWebSocketError("candle timestamp must be a finite integer")
    timestamp = int(raw_t)
    if timestamp <= 0 or timestamp % RESOLUTIONS[resolution] != 0:
        raise NobitexWebSocketError("candle timestamp is invalid or misaligned")
    if type(received_at) is not int or received_at <= 0:
        raise NobitexWebSocketError("received_at must be a positive integer")
    if timestamp > received_at + 5:
        raise NobitexWebSocketError("candle timestamp is in the future")
    values = []
    for key in ("o","h","l","c","v"):
        value = payload[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise NobitexWebSocketError(f"candle {key} must be numeric")
        number = float(value)
        if not math.isfinite(number):
            raise NobitexWebSocketError(f"candle {key} must be finite")
        values.append(number)
    op, high, low, close, volume = values
    if min(op, high, low, close) <= 0 or volume < 0:
        raise NobitexWebSocketError("candle prices or volume are outside valid bounds")
    if high < max(op, close) or low > min(op, close) or high < low:
        raise NobitexWebSocketError("candle OHLC relationship is invalid")
    seconds = RESOLUTIONS[resolution]
    return {
        "source": "Nobitex public WebSocket", "symbol": symbol,
        "resolution": resolution, "timestamp": timestamp,
        "received_at": received_at, "open": op, "high": high, "low": low,
        "close": close, "volume": volume,
        "is_closed": received_at >= timestamp + seconds,
    }

class NobitexCandleWebSocket:
    """Bounded, reconnecting reader for the six public candle channels."""
    def __init__(self, *, websocket_app_factory: Callable | None = None,
                 wall_clock: Callable[[], float] = time.time,
                 monotonic: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.websocket_app_factory = websocket_app_factory
        self.wall_clock, self.monotonic, self.sleep = wall_clock, monotonic, sleep
        self.counts = {channel: 0 for channel in channel_map()}
        self.errors: list[str] = []

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
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        deadline = self.monotonic() + duration_seconds
        delay = 1.0
        with path.open("a", encoding="utf-8") as output:
            while self.monotonic() < deadline:
                next_id = {"value": 2}
                def on_open(app):
                    app.send(json.dumps({"connect": {}, "id": 1}, separators=(",", ":")))
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
                    if frame.get("id") == 1 and "connect" in frame:
                        for channel in channel_map():
                            request_id = next_id["value"]
                            next_id["value"] += 1
                            app.send(json.dumps({"id": request_id,
                                "subscribe": {"channel": channel}}, separators=(",", ":")))
                        return
                    push = frame.get("push")
                    if not isinstance(push, dict):
                        return
                    channel, pub = push.get("channel"), push.get("pub")
                    if channel not in channel_map() or not isinstance(pub, dict):
                        return
                    data = pub.get("data")
                    if isinstance(data, str):
                        try:
                            data = json.loads(data)
                        except json.JSONDecodeError:
                            self.errors.append("invalid_candle_json")
                            return
                    try:
                        record = parse_candle_publication(
                            channel, data, received_at=int(self.wall_clock()))
                    except NobitexWebSocketError as exc:
                        self.errors.append(str(exc))
                        return
                    output.write(json.dumps(record, sort_keys=True) + "\n")
                    output.flush()
                    self.counts[channel] += 1
                def on_error(_app, error):
                    self.errors.append(type(error).__name__)
                app = factory(WEBSOCKET_URL, on_open=on_open, on_message=on_message,
                              on_error=on_error, on_close=lambda *_: None)
                timer = threading.Timer(max(0.1, deadline - self.monotonic()), app.close)
                timer.daemon = True
                timer.start()
                try:
                    app.run_forever(ping_interval=20, ping_timeout=10)
                except Exception as exc:
                    self.errors.append(type(exc).__name__)
                finally:
                    timer.cancel()
                    try: app.close()
                    except Exception: pass
                if self.monotonic() >= deadline:
                    break
                self.sleep(min(delay, max(0.0, deadline - self.monotonic())))
                delay = min(delay * 2, 30.0)
        total = sum(self.counts.values())
        return {"status": "ok" if total else "no_publications",
                "url": WEBSOCKET_URL, "duration_seconds": duration_seconds,
                "publications_by_channel": dict(self.counts),
                "total_publications": total, "errors": list(self.errors),
                "output_path": str(path)}
