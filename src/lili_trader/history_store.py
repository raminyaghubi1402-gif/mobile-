"""Persistent, deduplicated CSV store for public Nobitex OHLC history."""
from __future__ import annotations

import csv
from pathlib import Path
from math import isfinite
from typing import Iterable

from .nobitex_data import NobitexCandle


_FIELDS = ("timestamp", "open", "high", "low", "close", "volume")


def merge_candle_history(
    existing: Iterable[NobitexCandle],
    incoming: Iterable[NobitexCandle],
    *,
    max_rows: int = 20_000,
) -> list[NobitexCandle]:
    if not isinstance(max_rows, int) or max_rows < 1:
        raise ValueError("max_rows must be a positive integer")
    merged: dict[int, NobitexCandle] = {}
    for candle in list(existing) + list(incoming):
        values = (candle.open, candle.high, candle.low, candle.close, candle.volume)
        if (candle.timestamp < 0 or any(not isfinite(v) or v < 0 for v in values)
                or candle.low <= 0 or candle.high < max(candle.open, candle.close)
                or candle.low > min(candle.open, candle.close)):
            raise ValueError("invalid OHLC candle in history store")
        merged[candle.timestamp] = candle
    return sorted(merged.values(), key=lambda row: row.timestamp)[-max_rows:]


def load_history_csv(
    path: str | Path,
    *,
    symbol: str,
    resolution: str,
) -> list[NobitexCandle]:
    file_path = Path(path)
    if not file_path.exists():
        return []
    rows = []
    with file_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != _FIELDS:
            raise ValueError("unexpected history CSV columns")
        for row in reader:
            try:
                rows.append(NobitexCandle(
                    symbol=symbol,
                    resolution=resolution,
                    timestamp=int(row["timestamp"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                    source="Persisted Nobitex public history",
                ))
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError("invalid history CSV row") from exc
    return merge_candle_history([], rows, max_rows=max(1, len(rows)))


def merge_history_csv(
    path: str | Path,
    incoming: Iterable[NobitexCandle],
    *,
    max_rows: int = 20_000,
) -> list[NobitexCandle]:
    file_path = Path(path)
    incoming_rows = list(incoming)
    if not incoming_rows:
        return load_history_csv(
            file_path,
            symbol="UNKNOWN",
            resolution="UNKNOWN",
        ) if file_path.exists() else []
    symbol, resolution = incoming_rows[0].symbol, incoming_rows[0].resolution
    existing = load_history_csv(file_path, symbol=symbol, resolution=resolution)
    merged = merge_candle_history(existing, incoming_rows, max_rows=max_rows)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = file_path.with_suffix(file_path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_FIELDS)
        writer.writeheader()
        for candle in merged:
            writer.writerow({
                "timestamp": candle.timestamp,
                "open": f"{candle.open:.12g}",
                "high": f"{candle.high:.12g}",
                "low": f"{candle.low:.12g}",
                "close": f"{candle.close:.12g}",
                "volume": f"{candle.volume:.12g}",
            })
    temporary.replace(file_path)
    return merged
