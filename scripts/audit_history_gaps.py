#!/usr/bin/env python3
"""Fail-closed preflight audit for stored OHLC candle interval continuity."""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

EXPECTED_SECONDS = {
    "1": 60, "5": 300, "15": 900, "30": 1800, "60": 3600,
    "180": 10800, "240": 14400, "360": 21600, "720": 43200,
    "D": 86400, "2D": 172800, "3D": 259200,
}
FIELDS = ("timestamp", "open", "high", "low", "close", "volume")


def _utc(timestamp: int | None) -> str | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def audit_csv(path: str | Path, resolution: str) -> dict:
    """Return a data-quality report; never fills or repairs missing candles."""
    file_path = Path(path)
    expected_seconds = EXPECTED_SECONDS.get(resolution)
    report = {
        "path": str(file_path),
        "resolution": resolution,
        "expected_interval_seconds": expected_seconds,
        "row_count": 0,
        "first_timestamp": None,
        "last_timestamp": None,
        "issues": [],
        "missing_candle_count": 0,
        "gap_count": 0,
        "clean": False,
    }
    if expected_seconds is None:
        report["issues"].append({"type": "unsupported_resolution", "value": resolution})
        return report
    if not file_path.is_file():
        report["issues"].append({"type": "missing_file"})
        return report

    try:
        with file_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if tuple(reader.fieldnames or ()) != FIELDS:
                report["issues"].append({
                    "type": "invalid_header",
                    "expected": list(FIELDS),
                    "actual": list(reader.fieldnames or ()),
                })
                return report
            timestamps = []
            for row_number, row in enumerate(reader, start=2):
                raw = row.get("timestamp")
                try:
                    if raw is None or not raw.strip() or not raw.strip().isdigit():
                        raise ValueError("timestamp must be a positive integer")
                    timestamp = int(raw)
                    if timestamp <= 0:
                        raise ValueError("timestamp must be positive")
                except (TypeError, ValueError, OverflowError) as exc:
                    report["issues"].append({
                        "type": "invalid_timestamp",
                        "row": row_number,
                        "value": raw,
                        "reason": str(exc),
                    })
                    continue
                timestamps.append(timestamp)
    except (OSError, UnicodeError, csv.Error) as exc:
        report["issues"].append({"type": "read_error", "reason": type(exc).__name__})
        return report

    report["row_count"] = len(timestamps)
    if not timestamps:
        report["issues"].append({"type": "empty_history"})
        return report
    report["first_timestamp"] = timestamps[0]
    report["last_timestamp"] = timestamps[-1]

    for index in range(1, len(timestamps)):
        previous, current = timestamps[index - 1], timestamps[index]
        delta = current - previous
        if delta == expected_seconds:
            continue
        issue = {
            "type": "duplicate" if delta == 0 else "out_of_order" if delta < 0
                    else "gap" if delta % expected_seconds == 0 else "irregular_interval",
            "previous_timestamp": previous,
            "timestamp": current,
            "delta_seconds": delta,
            "expected_interval_seconds": expected_seconds,
        }
        if issue["type"] == "gap":
            issue["missing_candles"] = delta // expected_seconds - 1
            report["missing_candle_count"] += issue["missing_candles"]
        report["issues"].append(issue)

    report["first_timestamp_utc"] = _utc(report["first_timestamp"])
    report["last_timestamp_utc"] = _utc(report["last_timestamp"])
    report["gap_count"] = sum(issue["type"] == "gap" for issue in report["issues"])
    report["clean"] = not report["issues"]
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True, choices=("BTCIRT", "ETHIRT"))
    parser.add_argument("--resolution", required=True, action="append", choices=tuple(EXPECTED_SECONDS))
    parser.add_argument("--history-dir", default="data/history")
    parser.add_argument("--output", default="history-gap-audit.json")
    args = parser.parse_args()

    reports = [
        audit_csv(Path(args.history_dir) / f"{args.symbol}_{resolution}.csv", resolution)
        for resolution in args.resolution
    ]
    result = {
        "symbol": args.symbol,
        "audited_at_utc": datetime.now(timezone.utc).isoformat(),
        "clean": all(report["clean"] for report in reports),
        "files": reports,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    for report in reports:
        start, end = report.get("first_timestamp_utc"), report.get("last_timestamp_utc")
        print(
            f"{args.symbol} {report['resolution']}m: rows={report['row_count']} "
            f"range={start or 'n/a'}..{end or 'n/a'} "
            f"gaps={report.get('gap_count', 0)} "
            f"missing_candles={report['missing_candle_count']} "
            f"issues={len(report['issues'])}"
        )
        for issue in report["issues"][:10]:
            print(f"  - {issue}")
        if len(report["issues"]) > 10:
            print(f"  - ... {len(report['issues']) - 10} additional issue(s)")
    if result["clean"]:
        print("Preflight PASS: required timeframes are continuous. No data was modified.")
        return 0
    print("Preflight FAIL: gaps or malformed history found; backtest is blocked. No candles were fabricated.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
