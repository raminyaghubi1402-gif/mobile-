# Li-Li Trader — Realistic Backtest & Paper-Trading Standard

**Status:** Mandatory research standard. No live order execution is authorized by this document.

## Core principle
Backtests and paper tests must approximate executable market conditions, not idealized candle-price outcomes. Never tune assumptions merely to make results profitable. A result is not evidence of future profitability.

## Capital and denomination
- User-designated research equity: **10 USD** (simulation only; never infer that this is real exchange balance).
- Keep account currency, quote currency, and conversion rate separate.
- For IRT-quoted Nobitex pairs, do not pass `10` as local cash and label it USD.
- A USD-to-IRT conversion must record: rate, source, timestamp, and whether it is USD/IRT or USDT/IRT proxy. USDT is not automatically identical to USD.
- If a defensible rate is unavailable, stop the USD-denominated simulation or clearly mark it as unconverted; do not silently substitute a rate.
- Reports must show original USD capital, converted local-currency capital, conversion assumptions, and units for every monetary amount.

## Execution realism
Every backtest must disclose and, where data allows, model:
- exchange fee tier and maker/taker assumption;
- spread and slippage, including adverse slippage;
- order type and whether the order could fill;
- minimum order size, minimum notional, precision/step-size and available balance;
- market liquidity/order-book depth and market impact for the simulated size;
- gaps through stops, partial fills, rejected orders, and unavailable liquidity;
- funding, borrow, taxes, and other costs when applicable (otherwise explicitly mark excluded);
- no leverage unless separately approved and realistically modeled; current project is analysis-only.

A candle touching a limit price is **not proof of a fill**. OHLC alone cannot establish intrabar sequence, queue position, or order-book liquidity. If data cannot resolve these, use conservative assumptions and disclose uncertainty.

## Data integrity and anti-lookahead
- Use timestamped real market data with documented source and collection time.
- Align 1h/15m/5m candles by close time; never use a candle before it closes.
- Entry is not at the signal candle's already-known close unless the execution model explicitly supports that order.
- Check gaps, duplicates, malformed candles, stale data, symbol/market changes, and timezone.
- Report native vs resampled candles separately; never treat synthetic candles as native exchange observations.
- Require meaningful multi-regime coverage (trend up/down, range, volatility changes), not only a short favorable interval.
- Keep a fixed holdout period and avoid selecting parameters based on that holdout.

## Risk and strategy behavior
- Existing strategy rules remain: trend/regime first; BUY-only in confirmed uptrend, SELL-only in confirmed downtrend, no trade in range/uncertain/transition; never countertrend.
- Risk sizing must use equity, stop distance, fees, minimum order constraints, and available capital. If the resulting size cannot be placed under exchange rules, skip the trade.
- Stops must be evaluated with gap risk and realistic execution; a stop price is not a guaranteed fill price.
- Record skipped trades and reasons, not only completed trades.

## Required report fields
Each run must include:
1. market/symbol, data source, data range, timestamp/timezone, native/resampled counts and overlap;
2. capital and currency conversion details;
3. fee, spread, slippage, fill, and other execution assumptions;
4. trade count, skipped signals, win rate, profit factor, net P&L, return, max drawdown, and exposure;
5. breakdown by BUY/SELL, regime, strategy family, and exit reason where available;
6. baseline comparison and sensitivity analysis (costs/slippage worsened);
7. data-quality warnings and limitations;
8. explicit label: **research/backtest only; not a live-trading recommendation**.

## Fail-closed conditions
Do not present a run as realistic or actionable when:
- conversion is missing for USD-denominated capital;
- required market history is too short or timeframe overlap is inadequate;
- fee/minimum-order rules are unknown;
- data is stale, malformed, or has unresolved gaps;
- the simulated trade cannot meet exchange minimums or liquidity assumptions;
- test code fails or results cannot be reproduced.

In such cases, label the run **INCOMPLETE / NOT VALIDATED**, explain the blocker, and do not claim strategy profitability.

## Current scope
- Nobitex public-market data; analysis and backtesting only.
- No broker connection, real orders, leverage, or unattended live trading.
- The 10 USD amount is a simulation starting point only and must never be treated as funds available for actual trading.
