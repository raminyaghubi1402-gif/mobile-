# Trend Pullback historical diagnostic

The independent research engine is replayed at each 5m candle close using only H1, M15, and M5 candles whose full interval has closed by the snapshot time. Each timeframe must be fresh and the latest two bars cannot contain a large gap. Results are split chronologically 70/30.

- Counts SIGNAL/WAIT/NO_SETUP/INVALIDATED and skipped stale/gap snapshots.
- For SIGNALs, reports forward close return and favorable/adverse excursion normalized by contiguous ATR(14), over 3/6/12 5m bars.
- Train horizons must end before the split; holdout uses the final 30%.
- No entries, SL/TP, sizing, fees, slippage, PnL, or execution simulation.
- No optimization or profitability claim. This is an engine smoke/research diagnostic; the engine remains unconnected to Paper.

Run: `PYTHONPATH=src python scripts/trend_pullback_historical_diagnostic.py`.
