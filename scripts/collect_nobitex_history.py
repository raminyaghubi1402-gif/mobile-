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


# Focused validation run: only the most recent complete UTC-aligned day.
# Expand the history window only after this exact 96-candle test passes.
SYMBOL_FETCH_COUNTS = {
    "BTCUSDT": {"15": 96},
}
BTCUSDT_15M_REQUIRED_CANDLES = 96
MAX_ROWS = 25_000


def fetch_btcusdt_15m_window(adapter: NobitexAdapter, *, now: int | None = None) -> list:
    """Fetch one exact closed 15m day, including the final candle's full time range."""
    import time

    observed_at = int(time.time()) if now is None else now
    if type(observed_at) is not int or observed_at <= 0:
        raise ValueError("now must be a positive Unix timestamp")

    interval = 900
    latest_closed = (observed_at // interval) * interval - interval
    first_expected = latest_closed - (BTCUSDT_15M_REQUIRED_CANDLES - 1) * interval
    # Nobitex defines `to` as the end of the requested time range. Include
    # the complete final candle, but accept only candle-start timestamps up
    # to latest_closed so no in-progress/future candle can enter the window.
    range_end = latest_closed + interval - 1
    collected = {}

    # Request one guard candle before the target window because the upstream
    # UDF endpoint may treat the "from" boundary as exclusive.
    cursor = first_expected - interval
    while cursor <= range_end:
        chunk_end = min(
            cursor + (500 - 1) * interval,
            range_end,
        )
        batch = adapter.fetch_recent(
            "BTCUSDT", "15", 500,
            page=1,
            from_timestamp=cursor,
            to_timestamp=chunk_end,
        )
        for candle in batch:
            if candle.timestamp % interval != 0:
                raise RuntimeError(
                    f"unaligned BTCUSDT 15m timestamp: {candle.timestamp}"
                )
            if candle.timestamp < cursor or candle.timestamp > latest_closed:
                raise RuntimeError(
                    "Nobitex returned BTCUSDT candle outside requested closed-candle window"
                )
            if candle.timestamp in collected:
                raise RuntimeError(f"duplicate BTCUSDT 15m timestamp: {candle.timestamp}")
            collected[candle.timestamp] = candle
        cursor = chunk_end + interval

    # Retry through countback using the same full-candle end boundary.
    # Merge only source-returned candles; never synthesize a missing bar.
    fallback = adapter.fetch_recent(
        "BTCUSDT", "15", BTCUSDT_15M_REQUIRED_CANDLES,
        page=1, to_timestamp=range_end,
    )
    for candle in fallback:
        if first_expected <= candle.timestamp <= latest_closed:
            if candle.timestamp % interval != 0:
                raise RuntimeError(
                    f"unaligned BTCUSDT 15m timestamp: {candle.timestamp}"
                )
            collected.setdefault(candle.timestamp, candle)

    expected = range(first_expected, latest_closed + interval, interval)
    missing = [timestamp for timestamp in expected if timestamp not in collected]
    if missing:
        raise RuntimeError(
            f"BTCUSDT 15m continuity FAIL: expected={BTCUSDT_15M_REQUIRED_CANDLES}, "
            f"received={len(collected)}, missing={len(missing)}, "
            f"first_missing={missing[0]}"
        )
    incoming = [collected[timestamp] for timestamp in expected]
    if len(incoming) != BTCUSDT_15M_REQUIRED_CANDLES:
        raise RuntimeError("BTCUSDT 15m exact-window validation failed")
    print(
        f"BTCUSDT 15m continuity PASS: candles={len(incoming)} "
        f"first={incoming[0].timestamp} last={incoming[-1].timestamp} "
        f"interval_seconds={interval} window_days=1"
    )
    return incoming

def main() -> int:
    adapter = NobitexAdapter()
    history_dir = ROOT / "data" / "history"
    failures = []
    completed_requests = 0
    for symbol, fetch_counts in SYMBOL_FETCH_COUNTS.items():
        for resolution, fetch_count in fetch_counts.items():
            try:
                if symbol == "BTCUSDT" and resolution == "15":
                    incoming = fetch_btcusdt_15m_window(adapter)
                else:
                    incoming = adapter.fetch_history(symbol, resolution, fetch_count)
                completed_requests += 1
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
        if any(item.startswith("BTCUSDT 15:") for item in failures):
            print("Required BTCUSDT 15m history failed validation; refusing to publish or backtest this run.", file=sys.stderr)
            return 1
        if completed_requests == 0:
            return 1
        print("Partial BTCIRT/ETHIRT history was saved; failed timeframes will be retried on the next scheduled run.")
        return 0
    print("Public Nobitex history collection completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
