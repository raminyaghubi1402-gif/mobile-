import csv

from lili_trader.__main__ import _load_backtest_history
from lili_trader.nobitex_data import NobitexCandle


def _write_history(path):
    rows = [
        {"timestamp": 60, "open": 100, "high": 105, "low": 95, "close": 102, "volume": 1},
        {"timestamp": 120, "open": 102, "high": 106, "low": 99, "close": 104, "volume": 2},
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["timestamp", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        writer.writerows(rows)


class FakeAdapter:
    def __init__(self):
        self.calls = []

    def fetch_history(self, symbol, resolution, count):
        self.calls.append((symbol, resolution, count))
        return [
            NobitexCandle(
                symbol=symbol, resolution=resolution, timestamp=180,
                open=104, high=108, low=103, close=107, volume=3,
            )
        ]


def test_stored_history_only_never_calls_api(tmp_path):
    path = tmp_path / "BTCIRT_5.csv"
    _write_history(path)
    adapter = FakeAdapter()

    candles = _load_backtest_history(path, "BTCIRT", "5", 100, adapter, stored_only=True)

    assert len(candles) == 2
    assert [c.timestamp for c in candles] == [60, 120]
    assert adapter.calls == []


def test_backtest_history_refresh_mode_calls_api(tmp_path):
    path = tmp_path / "BTCIRT_5.csv"
    _write_history(path)
    adapter = FakeAdapter()

    candles = _load_backtest_history(path, "BTCIRT", "5", 100, adapter, stored_only=False)

    assert len(candles) == 3
    assert [c.timestamp for c in candles] == [60, 120, 180]
    assert adapter.calls == [("BTCIRT", "5", 100)]
