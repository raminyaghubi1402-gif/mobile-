# BOS / CHoCH historical diagnostic

Exploratory descriptive audit of confirmed structure using checked-in Nobitex BTCIRT/ETHIRT candles at 5m, 15m, 1h, 4h and daily. It is **not** a strategy backtest and cannot establish profitability.

- **Level-reclaim failure:** an up-break is labeled reclaimed if a later close at/below its broken level occurs within the horizon; down-break uses a close at/above. This operational label is not ground truth for a false breakout.
- **Favorable/adverse excursion:** max favorable/adverse high-low movement from event-candle close over next 3, 6, 12 candles, normalized by ATR(14) calculated through the event candle only.
- Six-bar event-class comparison separates BOS, CHoCH and initial BREAK.
- **MTF conditioning:** 5m events grouped by 1h direction and by simultaneous 1h/15m alignment; 15m events grouped by 1h direction. Higher-TF candles are included only when the full candle interval closed by the lower-TF event candle's close.
- Only fully completed forward horizons are included; prefix causality is checked at 25%, 50%, 75% of history.
- Gaps are warnings; duplicate/nonmonotonic timestamps, malformed OHLC, insufficient data or prefix mismatch fail.

No entries, stop/targets, fees, slippage, PnL, broker calls, or live trading modeled. This is a research diagnostic only.

Run `PYTHONPATH=src python scripts/bos_choch_historical_diagnostic.py`. CI uploads the report artifact.
