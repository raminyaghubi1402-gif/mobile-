"""Conservative recovery of missing 5m candles from the public Nobitex API.

Only exact timestamps confirmed by the API are inserted. This module never
synthesizes flat candles or extrapolates prices/volume.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from .history_store import merge_history_csv
from .nobitex_data import NobitexAdapter

_FIELDS = ("timestamp", "open", "high", "low", "close", "volume")
_INTERVAL_SECONDS = 300
_MAX_API_CANDLE_COUNT = 500
_DEFAULT_MAX_GAP_QUERIES = 1
_MAX_ROWS = 25_000


def _read_timestamps(path: Path) -> tuple[list[int] | None, str | None]:
    if not path.is_file():
        return None, "missing_history"
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if tuple(reader.fieldnames or ()) != _FIELDS:
                return None, "invalid_header"
            timestamps = []
            for row in reader:
                raw = row.get("timestamp")
                if raw is None or not raw.strip() or not raw.strip().isdigit():
                    return None, "invalid_timestamp"
                timestamp = int(raw)
                if timestamp <= 0:
                    return None, "invalid_timestamp"
                timestamps.append(timestamp)
    except (OSError, UnicodeError, csv.Error):
        return None, "read_error"
    if not timestamps:
        return [], "empty_history"
    if any(current <= previous for previous, current in zip(timestamps, timestamps[1:])):
        return None, "non_chronological"
    return timestamps, None


def _missing_intervals(timestamps: list[int]) -> tuple[list[list[int]] | None, str | None]:
    gaps = []
    for previous, current in zip(timestamps, timestamps[1:]):
        delta = current - previous
        if delta == _INTERVAL_SECONDS:
            continue
        if delta <= 0 or delta % _INTERVAL_SECONDS:
            return None, "irregular_interval"
        missing = list(range(previous + _INTERVAL_SECONDS, current, _INTERVAL_SECONDS))
        gaps.append(missing)
    return gaps, None


def recover_missing_5m_candles(
    path: str | Path,
    symbol: str,
    adapter: NobitexAdapter,
    *,
    max_gap_queries: int = _DEFAULT_MAX_GAP_QUERIES,
) -> dict:
    """Attempt bounded API retrieval for exact missing 5m timestamps.

    A gap of more than 500 bars is not queried because the public UDF endpoint
    caps each response at 500 bars. At most one gap is queried per symbol by
    default to keep the collector's additional request budget small.
    """
    if symbol not in {"BTCIRT", "ETHIRT"}:
        raise ValueError("gap recovery is limited to BTCIRT and ETHIRT")
    if type(max_gap_queries) is not int or max_gap_queries < 1:
        raise ValueError("max_gap_queries must be a positive integer")

    file_path = Path(path)
    timestamps, read_error = _read_timestamps(file_path)
    if read_error:
        return {
            "status": read_error,
            "gap_intervals_before": 0,
            "missing_candles_before": 0,
            "missing_timestamps_before": [],
            "attempted_queries": 0,
            "recovered_timestamps": [],
            "recovered_from_1m": [],
            "gap_intervals_after": 0,
            "missing_candles_after": 0,
            "skipped_intervals": 0,
            "errors": [],
        }

    gaps, gap_error = _missing_intervals(timestamps or [])
    if gap_error:
        return {
            "status": gap_error,
            "gap_intervals_before": 0,
            "missing_candles_before": 0,
            "attempted_queries": 0,
            "recovered_timestamps": [],
            "recovered_from_1m": [],
            "gap_intervals_after": 0,
            "missing_candles_after": 0,
            "skipped_intervals": 0,
            "errors": [],
        }

    gap_intervals_before = len(gaps)
    missing_candles_before = sum(len(gap) for gap in gaps)
    if not gaps:
        return {
            "status": "clean",
            "gap_intervals_before": 0,
            "missing_candles_before": 0,
            "attempted_queries": 0,
            "recovered_timestamps": [],
            "recovered_from_1m": [],
            "gap_intervals_after": 0,
            "missing_candles_after": 0,
            "skipped_intervals": 0,
            "errors": [],
        }

    # Recent gaps are most relevant to the current backtest; try those first.
    eligible = [gap for gap in reversed(gaps) if len(gap) <= _MAX_API_CANDLE_COUNT]
    skipped_intervals = len(gaps) - len(eligible)
    selected = eligible[:max_gap_queries]
    skipped_intervals += len(eligible) - len(selected)

    recovered_timestamps: list[int] = []
    recovered_from_1m: list[int] = []
    errors: list[str] = []
    attempted_queries = 0
    for missing in selected:
        attempted_queries += 1
        try:
            # First request the exact native 5m interval from Nobitex.
            fetched = adapter.fetch_recent(
                symbol, "5", _MAX_API_CANDLE_COUNT,
                page=1, from_timestamp=missing[0],
                to_timestamp=missing[-1] + _INTERVAL_SECONDS - 1,
            )
        except Exception as exc:
            errors.append(type(exc).__name__)
            fetched = []

        missing_set = set(missing)
        exact = [candle for candle in fetched if candle.timestamp in missing_set]
        if exact:
            try:
                merge_history_csv(file_path, exact, max_rows=_MAX_ROWS)
            except (OSError, ValueError, TypeError) as exc:
                errors.append(type(exc).__name__)
                continue
            recovered_timestamps.extend(candle.timestamp for candle in exact)

        # If native 5m data is unavailable, attempt a strictly lossless
        # aggregation from the five exact Nobitex 1m candles. Never interpolate
        # missing minutes, carry prices forward, or borrow another exchange's
        # prices. Provenance is persisted in a sidecar manifest.
        remaining = [timestamp for timestamp in missing if timestamp not in recovered_timestamps]
        for timestamp in remaining:
            attempted_queries += 1
            try:
                minute_rows = adapter.fetch_recent(
                    symbol, "1", 5, page=1,
                    from_timestamp=timestamp,
                    to_timestamp=timestamp + _INTERVAL_SECONDS - 1,
                )
            except Exception as exc:
                errors.append(type(exc).__name__)
                continue

            expected_minutes = [timestamp + offset * 60 for offset in range(5)]
            by_timestamp = {row.timestamp: row for row in minute_rows}
            if (
                len(minute_rows) != 5
                or len(by_timestamp) != 5
                or set(by_timestamp) != set(expected_minutes)
                or any(row.symbol != symbol or row.resolution != "1" for row in minute_rows)
                or timestamp % _INTERVAL_SECONDS != 0
            ):
                continue
            ordered = [by_timestamp[value] for value in expected_minutes]
            from .nobitex_data import NobitexCandle
            reconstructed = NobitexCandle(
                symbol=symbol,
                resolution="5",
                timestamp=timestamp,
                open=ordered[0].open,
                high=max(row.high for row in ordered),
                low=min(row.low for row in ordered),
                close=ordered[-1].close,
                volume=sum(row.volume for row in ordered),
                source="Nobitex public API 1m aggregation",
                fetched_at=max(row.fetched_at for row in ordered),
            )
            try:
                merge_history_csv(file_path, [reconstructed], max_rows=_MAX_ROWS)
                manifest_path = file_path.with_name(file_path.stem + "_recovery_manifest.jsonl")
                manifest_path.parent.mkdir(parents=True, exist_ok=True)
                existing = manifest_path.read_text(encoding="utf-8") if manifest_path.exists() else ""
                record = {
                    "symbol": symbol,
                    "resolution": "5",
                    "timestamp": timestamp,
                    "source": "Nobitex public API",
                    "method": "aggregate_exact_1m",
                    "source_timestamps": expected_minutes,
                    "ohlcv": {
                        "open": reconstructed.open,
                        "high": reconstructed.high,
                        "low": reconstructed.low,
                        "close": reconstructed.close,
                        "volume": reconstructed.volume,
                    },
                }
                temporary = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
                temporary.write_text(
                    existing + json.dumps(record, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                temporary.replace(manifest_path)
            except (OSError, ValueError, TypeError) as exc:
                errors.append(type(exc).__name__)
                continue
            recovered_timestamps.append(timestamp)
            recovered_from_1m.append(timestamp)

    final_timestamps, final_error = _read_timestamps(file_path)
    final_gaps, final_gap_error = _missing_intervals(final_timestamps or [])
    if final_error or final_gap_error:
        status = final_error or final_gap_error or "invalid_after_recovery"
        gap_intervals_after = 0
        missing_candles_after = 0
    else:
        gap_intervals_after = len(final_gaps)
        missing_candles_after = sum(len(gap) for gap in final_gaps)
        status = "clean" if missing_candles_after == 0 else "unresolved"

    return {
        "status": status,
        "gap_intervals_before": gap_intervals_before,
        "missing_candles_before": missing_candles_before,
        "missing_timestamps_before": sorted(timestamp for gap in gaps for timestamp in gap),
        "attempted_queries": attempted_queries,
        "recovered_timestamps": sorted(set(recovered_timestamps)),
        "recovered_from_1m": sorted(set(recovered_from_1m)),
        "gap_intervals_after": gap_intervals_after,
        "missing_candles_after": missing_candles_after,
        "missing_timestamps_after": sorted(timestamp for gap in final_gaps for timestamp in gap) if not final_error and not final_gap_error else [],
        "skipped_intervals": skipped_intervals,
        "errors": errors,
    }
