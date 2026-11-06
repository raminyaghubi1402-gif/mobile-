#!/usr/bin/env python3
"""Collect read-only public Nobitex candles into versioned CSV history."""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lili_trader.history_store import merge_history_csv
from lili_trader.nobitex_data import NobitexAdapter


SYMBOLS = ("BTCIRT", "ETHIRT")
FETCH_COUNTS = {"5": 5000, "15": 2000, "60": 2000, "240": 1000, "D": 1000}
MAX_ROWS = 20_000


def main() -> int:
    adapter = NobitexAdapter()
    history_dir = ROOT / "data" / "history"
    failures = []
    for symbol in SYMBOLS:
        for resolution, fetch_count in FETCH_COUNTS.items():
            try:
                incoming = adapter.fetch_history(symbol, resolution, fetch_count)
                if not incoming:
                    print(f"{symbol} {resolution}: no new public candles; kept existing history")
                    continue
                path = history_dir / f"{symbol}_{resolution}.csv"
                merged = merge_history_csv(path, incoming, max_rows=MAX_ROWS)
                print(
                    f"{symbol} {resolution}: received={len(incoming)} "
                    f"stored={len(merged)} oldest={merged[0].timestamp} "
                    f"newest={merged[-1].timestamp}"
                )
            except Exception as exc:
                failures.append(f"{symbol} {resolution}: {type(exc).__name__}: {exc}")
                print(f"ERROR {failures[-1]}", file=sys.stderr)
    if failures:
        print(f"Collection finished with {len(failures)} failure(s).", file=sys.stderr)
        return 1
    print("Public Nobitex history collection completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
