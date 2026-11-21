# BOS / CHoCH historical diagnostic

This is an exploratory, descriptive audit of the confirmed market-structure engine using checked-in Nobitex BTCIRT/ETHIRT candle history across 5m, 15m, 1h, 4h and daily data. It is **not** a strategy backtest and does not establish profitability.

## Definitions
- **Level-reclaim failure:** after an up-break event, a later close at/below the broken level within the selected horizon; for a down-break, a close at/above the broken level. This simple operational label is not ground truth for a false breakout.
- **Favorable/adverse excursion:** maximum favorable/adverse high/low movement from event-candle close over the next 3, 6 or 12 candles, normalized by ATR(14) calculated only through the event candle.
- A 6-bar summary also separates BOS, CHoCH, and initial BREAK event classes. Sample sizes are reported; they may be small and are not evidence of predictive power.
- Only fully completed forward horizons are included. End-of-file events are excluded from that horizon's denominator.
- Prefix-causality compares full-history events with event lists generated from earlier prefixes (25%, 50%, 75%). A mismatch fails the diagnostic.
- Estimated candle gaps are warnings; duplicates, nonmonotonic timestamps, malformed OHLC or insufficient history fail.

No entries, stop/target orders, fees, slippage, PnL, broker calls, or trading are modeled. Results are descriptive research diagnostics only.

Run:
`PYTHONPATH=src python scripts/bos_choch_historical_diagnostic.py`

The report is written as `bos-choch-historical-diagnostic.txt` and uploaded by CI.
