#!/usr/bin/env python3
"""Collect read-only public Nobitex candles into versioned CSV history."""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lili_trader.history_gap_recovery import recover_missing_5m_candles
from lili_trader.history_store import merge_history_csv
from lili_trader.nobitex_data import NobitexAdapter


SYMBOL_FETCH_COUNTS = {
    "BTCIRT": {"1": 15_000, "5": 5000, "15": 2000, "60": 2000, "240": 1000, "D": 1000},
    "ETHIRT": {"1": 15_000, "5": 5000, "15": 2000, "60": 2000, "240": 1000, "D": 1000},
    # Dedicated research feed: 60 full days of 15-minute BTC/USDT candles.
    "BTCUSDT": {"15": 5780},
}
BTCUSDT_15M_REQUIRED_CANDLES = 5760
MAX_ROWS = 25_000


def main() -> int:
    adapter = NobitexAdapter()
    history_dir = ROOT / "data" / "history"
    failures = []
    completed_requests = 0
    for symbol, fetch_counts in SYMBOL_FETCH_COUNTS.items():
        for resolution, fetch_count in fetch_counts.items():
            try:
                incoming = adapter.fetch_history(symbol, resolution, fetch_count)
                completed_requests += 1
                if symbol == "BTCUSDT" and resolution == "15":
                    # Only persist a complete, closed-candle 60-day window.
                    # The API may include the currently forming 15m candle;
                    # exclude it and validate every expected 900-second slot.
                    interval = 900
                    import time
                    latest_closed = (int(time.time()) // interval) * interval - interval
                    first_expected = latest_closed - (BTCUSDT_15M_REQUIRED_CANDLES - 1) * interval
                    by_timestamp = {candle.timestamp: candle for candle in incoming}
                    expected = range(first_expected, latest_closed + interval, interval)
                    missing = [timestamp for timestamp in expected if timestamp not in by_timestamp]
                    if missing:
                        raise RuntimeError(
                            f"BTCUSDT 15m continuity FAIL: expected={BTCUSDT_15M_REQUIRED_CANDLES}, "
                            f"received={len(by_timestamp)}, missing={len(missing)}, "
                            f"first_missing={missing[0]}"
                        )
                    incoming = [by_timestamp[timestamp] for timestamp in expected]
                    if len(incoming) != BTCUSDT_15M_REQUIRED_CANDLES:
                        raise RuntimeError("BTCUSDT 15m exact-window validation failed")
                    print(
                        f"BTCUSDT 15m continuity PASS: candles={len(incoming)} "
                        f"first={incoming[0].timestamp} last={incoming[-1].timestamp} "
                        f"interval_seconds={interval} window_days=60"
                    )
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

        # Gap recovery applies only to the existing BTCIRT/ETHIRT 5m stores.
        # BTCUSDT is intentionally collected as a strict standalone 15m feed.
        if symbol not in ("BTCIRT", "ETHIRT"):
            continue

        # If the 5m store has an exact interval gap, make one bounded request
        # around the newest gap. Insert only source-confirmed candles; no flat
        # synthetic candles or extrapolated values are ever created.
        try:
            recovery = recover_missing_5m_candles(
                history_dir / f"{symbol}_5.csv", symbol, adapter,
            )
            print(
                f"{symbol} 5m gap recovery: "
                f"gaps={recovery['gap_intervals_before']} "
                f"missing={recovery['missing_candles_before']} "
                f"requests={recovery['attempted_queries']} "
                f"recovered={len(recovery['recovered_timestamps'])} "
                f"from_1m={len(recovery['recovered_from_1m'])} "
                f"remaining={recovery['missing_candles_after']} "
                f"status={recovery['status']}"
            )
            if recovery["errors"]:
                print(
                    f"{symbol} 5m recovery errors: "
                    f"{','.join(recovery['errors'])}",
                    file=sys.stderr,
                )
        except Exception as exc:
            # Recovery is opportunistic. A failure must not corrupt the store
            # or prevent the normal collection for the other symbol.
            print(
                f"WARNING {symbol} 5m gap recovery failed: {type(exc).__name__}",
                file=sys.stderr,
            )

    if failures:
        print(f"Collection finished with {len(failures)} failure(s) after {completed_requests} successful request group(s).", file=sys.stderr)
        if completed_requests == 0:
            return 1
        print("Partial history was saved; failed timeframes will be retried on the next scheduled run.")
        return 0
    print("Public Nobitex history collection completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
