"""Safe OHLC aggregation for deriving 15m context from public 5m candles."""
from __future__ import annotations

from collections import defaultdict
from .nobitex_data import NobitexCandle


def aggregate_five_to_fifteen(candles: list[NobitexCandle]) -> list[NobitexCandle]:
    """Aggregate only complete, aligned groups of three consecutive 5m bars."""
    groups: dict[int, list[NobitexCandle]] = defaultdict(list)
    for candle in candles:
        if candle.resolution != "5":
            raise ValueError("source candles must have 5m resolution")
        bucket = candle.timestamp - candle.timestamp % 900
        groups[bucket].append(candle)

    result = []
    for bucket, rows in sorted(groups.items()):
        rows.sort(key=lambda row: row.timestamp)
        if [row.timestamp for row in rows] != [bucket, bucket + 300, bucket + 600]:
            continue
        result.append(NobitexCandle(
            symbol=rows[0].symbol,
            resolution="15",
            timestamp=bucket,
            open=rows[0].open,
            high=max(row.high for row in rows),
            low=min(row.low for row in rows),
            close=rows[-1].close,
            volume=sum(row.volume for row in rows),
            source="Aggregated from Nobitex public 5m candles",
            fetched_at=max(row.fetched_at for row in rows),
        ))
    return result


def extend_fifteen_with_five(
    native_fifteen: list[NobitexCandle],
    five_minute: list[NobitexCandle],
) -> list[NobitexCandle]:
    """Extend native 15m history only with older, contiguous 5m-derived bars.

    Native 15m bars take precedence wherever both sources overlap. If the
    derived series cannot join seamlessly to the native series, it is ignored.
    """
    if not native_fifteen:
        return aggregate_five_to_fifteen(five_minute)
    if not five_minute:
        return list(native_fifteen)

    derived = aggregate_five_to_fifteen(five_minute)
    native = sorted(native_fifteen, key=lambda row: row.timestamp)
    older = [row for row in derived if row.timestamp < native[0].timestamp]
    if not older or older[-1].timestamp + 900 != native[0].timestamp:
        return native
    return older + native
