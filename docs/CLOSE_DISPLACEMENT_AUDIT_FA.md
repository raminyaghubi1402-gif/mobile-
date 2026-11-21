# Close-displacement research audit

For each available BTCIRT/ETHIRT timeframe, compare event close displacement beyond the broken structure level normalized by contiguous ATR(14), thresholds 0, 0.1, 0.25, 0.5, 1.0 ATR. Outcome horizon is six bars.

- Chronological 70%/30% split; report train and holdout independently.
- Require 15 consecutive candles for ATR(14) calculation and exact timestamp continuity through the forward horizon.
- Prefix-causality checks at 25%, 50%, 75% remain required.
- Labels: later close reclaiming the broken level; descriptive, not ground-truth false breakout.
- No entries/exits, risk sizing, fees, slippage, PnL, or execution simulation. Thresholds are a sensitivity grid, not optimized parameters.

Run: `PYTHONPATH=src:scripts python scripts/close_displacement_audit.py`.
