"""Conservative recovery of missing 5m candles from the public Nobitex API.

Only exact timestamps confirmed by the API are inserted. This module never
synthesizes flat candles or extrapolates prices/volume.
"""
from __future__ import annotations

import csv
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
            "attempted_queries": 0,
            "recovered_timestamps": [],
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
    errors: list[str] = []
    attempted_queries = 0
    for missing in selected:
        attempted_queries += 1
        try:
            # Countback is bounded to the documented 500-bar API maximum. The
            # requested end is immediately after the missing range so a candle
            # at an exact missing timestamp can be returned if it exists.
            fetched = adapter.fetch_recent(
                symbol, "5", _MAX_API_CANDLE_COUNT,
                page=1, from_timestamp=missing[0],
                to_timestamp=missing[-1] + _INTERVAL_SECONDS - 1,
            )
        except Exception as exc:
            errors.append(type(exc).__name__)
            continue

        missing_set = set(missing)
        exact = [candle for candle in fetched if candle.timestamp in missing_set]
        if not exact:
            continue
        try:
            merge_history_csv(file_path, exact, max_rows=_MAX_ROWS)
        except (OSError, ValueError, TypeError) as exc:
            errors.append(type(exc).__name__)
            continue
        recovered_timestamps.extend(candle.timestamp for candle in exact)

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
        "attempted_queries": attempted_queries,
        "recovered_timestamps": sorted(set(recovered_timestamps)),
        "gap_intervals_after": gap_intervals_after,
        "missing_candles_after": missing_candles_after,
        "skipped_intervals": skipped_intervals,
        "errors": errors,
    }
