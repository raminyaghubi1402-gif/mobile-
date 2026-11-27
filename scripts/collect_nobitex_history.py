#!/usr/bin/env python3
"""Collect read-only, closed Nobitex candles for the supported BTCIRT/ETHIRT markets."""
from __future__ import annotations

from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lili_trader.history_gap_recovery import recover_missing_5m_candles
from lili_trader.history_store import merge_history_csv
from lili_trader.nobitex_data import NobitexAdapter, NobitexCandle


# Keep the persisted history aligned with the live monitor's required
# resolutions. A single 500-bar request per series refreshes recent history;
# older stored rows are retained by merge_history_csv.
SYMBOL_FETCH_COUNTS = {
    "BTCIRT": {"5": 500, "15": 500, "60": 500},
    "ETHIRT": {"5": 500, "15": 500, "60": 500},
}
MAX_CANDLE_AGE_SECONDS = {"5": 900, "15": 1800, "60": 7200}
MAX_ROWS = 25_000


def closed_fresh_candles(
    candles: list[NobitexCandle],
    *,
    symbol: str,
    resolution: str,
    now: int,
) -> list[NobitexCandle]:
    """Keep only real, fully closed, fresh candles from the requested series."""
    if type(now) is not int or now <= 0:
        raise ValueError("now must be a positive Unix timestamp")
    if resolution not in MAX_CANDLE_AGE_SECONDS:
        raise ValueError("unsupported history-collector resolution")

    interval = NobitexAdapter.RESOLUTION_SECONDS[resolution]
    seen: set[int] = set()
    closed: list[NobitexCandle] = []
    for candle in candles:
        if not isinstance(candle, NobitexCandle):
            raise TypeError("history response contains a non-Nobitex candle")
        if candle.symbol != symbol or candle.resolution != resolution:
            raise ValueError("history response contains the wrong symbol or resolution")
        if type(candle.timestamp) is not int or candle.timestamp <= 0:
            raise ValueError("history response contains an invalid timestamp")
        if type(candle.fetched_at) is not int or candle.fetched_at <= 0:
            raise ValueError("history response is missing its fetch timestamp")
        if candle.timestamp in seen:
            raise ValueError("history response contains a duplicate timestamp")
        seen.add(candle.timestamp)

        candle_close_time = candle.timestamp + interval
        if candle_close_time <= candle.fetched_at and candle_close_time <= now:
            closed.append(candle)

    closed.sort(key=lambda candle: candle.timestamp)
    if not closed:
        raise RuntimeError(f"{symbol} {resolution}: no fully closed source candles returned")

    latest_close_time = closed[-1].timestamp + interval
    age = now - latest_close_time
    if age < 0 or age > MAX_CANDLE_AGE_SECONDS[resolution]:
        raise RuntimeError(
            f"{symbol} {resolution}: latest closed candle is stale "
            f"(age_seconds={age}, maximum={MAX_CANDLE_AGE_SECONDS[resolution]})"
        )
    return closed


def main() -> int:
    adapter = NobitexAdapter()
    history_dir = ROOT / "data" / "history"
    failures: list[str] = []

    for symbol, fetch_counts in SYMBOL_FETCH_COUNTS.items():
        for resolution, fetch_count in fetch_counts.items():
            try:
                incoming = adapter.fetch_history(symbol, resolution, fetch_count)
                closed = closed_fresh_candles(
                    incoming,
                    symbol=symbol,
                    resolution=resolution,
                    now=int(time.time()),
                )
                path = history_dir / f"{symbol}_{resolution}.csv"
                merged = merge_history_csv(path, closed, max_rows=MAX_ROWS)
                print(
                    f"{symbol} {resolution}: received={len(incoming)} "
                    f"closed={len(closed)} stored={len(merged)} "
                    f"oldest={merged[0].timestamp} newest={merged[-1].timestamp}"
                )
            except Exception as exc:
                failures.append(
                    f"{symbol} {resolution}: {type(exc).__name__}: {exc}"
                )
                print(f"ERROR {failures[-1]}", file=sys.stderr)

        # Gap recovery applies only to exact, source-confirmed BTCIRT/ETHIRT
        # 5m gaps. It never synthesizes or interpolates missing candles.
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
                    f"{symbol} 5m recovery errors: {','.join(recovery['errors'])}",
                    file=sys.stderr,
                )
        except Exception as exc:
            # Recovery is opportunistic; the original closed rows remain intact.
            print(
                f"WARNING {symbol} 5m gap recovery failed: {type(exc).__name__}",
                file=sys.stderr,
            )

    if failures:
        print(
            f"Collection failed closed: {len(failures)} required series failed. "
            "The workflow must not publish a partial history snapshot or run a backtest.",
            file=sys.stderr,
        )
        return 1

    print("Public Nobitex BTCIRT/ETHIRT closed-candle history collection completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
