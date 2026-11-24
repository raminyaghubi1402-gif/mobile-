import csv

from lili_trader.__main__ import _closed_backtest_history, _load_backtest_history
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



def _bar(timestamp):
    return NobitexCandle(
        symbol="BTCIRT", resolution="5", timestamp=timestamp,
        open=100, high=101, low=99, close=100, volume=1,
    )


def test_backtest_snapshot_drops_latest_row_even_after_its_interval_has_closed():
    candles = [_bar(300), _bar(600), _bar(900)]

    result = _closed_backtest_history(candles, 300, as_of_timestamp=2_000)

    assert [c.timestamp for c in result] == [300, 600]
    assert [c.timestamp for c in candles] == [300, 600, 900]


def test_backtest_snapshot_excludes_older_bars_not_closed_by_shared_cutoff():
    candles = [_bar(300), _bar(600), _bar(900)]

    result = _closed_backtest_history(candles, 300, as_of_timestamp=899)

    assert [c.timestamp for c in result] == [300]


def test_backtest_snapshot_rejects_invalid_timeframe_and_cutoff():
    candles = [_bar(300), _bar(600)]
    for timeframe in (True, 0, -300, 1.5):
        try:
            _closed_backtest_history(candles, timeframe, as_of_timestamp=2_000)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid timeframe accepted: {timeframe!r}")
    for cutoff in (True, 0, -1, 2_000.5):
        try:
            _closed_backtest_history(candles, 300, as_of_timestamp=cutoff)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid cutoff accepted: {cutoff!r}")


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
