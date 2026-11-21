# Pivot-window sensitivity audit

Compare BOS/CHoCH event output for pivot confirmation windows 1–5, BTCIRT/ETHIRT, and available 5m, 15m, 1h, 4h, daily history.

- Window 2 is reference; overlap keys use event kind, candle index and broken level.
- Report event counts, 6-candle reclaim outcome, median favorable/adverse ATR excursions, and favorable excursion >=1 ATR.
- Prefix causality at 25/50/75% history is checked for each window.
- Research only: no optimization, entries/exits, costs, slippage, PnL, or execution.

Run: `PYTHONPATH=src:scripts python scripts/pivot_window_sensitivity.py`.
