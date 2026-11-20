# Li-Li Trader — Smart Stop & Profit Management Contract

This contract applies to **every strategy engine** (spot and paper simulations only until explicitly authorized otherwise).

## Non-negotiable invariants
- No fixed-distance/fixed-percent stop is used as the universal stop rule.
- Every position must have a protective initial stop derived from market structure and volatility (ATR or equivalent); risk sizing must use that distance.
- A stop may only advance in the favorable direction: for long positions it can only rise, for short positions it can only fall. It must never retreat or release already locked profit.
- Stops must not be tightened merely because price pauses. Allow normal pullbacks using volatility-aware buffers and confirmed market structure.
- Profit protection is staged/continuous: protect break-even after sufficient favorable movement, then lock profit progressively, while allowing trend continuation.
- Exit or tighten when structure invalidates the thesis, volatility-adjusted trailing is reached, or a strategy-specific exit is confirmed. Do not force a fixed take-profit if the trend remains healthy.
- Costs, slippage, gaps, spread and stop-fill uncertainty must be included in validation; a stop is not a guarantee of execution price.

## Adaptive stop policy
- The shared manager accepts the latest ATR, best favorable extreme, and optional confirmed structure level from the engine.
- The ATR trail widens in higher volatility and may gradually tighten in quieter volatility. Adaptation is bounded, and monotonic-stop clamping takes precedence: widening ATR cannot loosen an already advanced stop.
- Structure candidates are buffered by a volatility-scaled distance. Reject levels on the wrong side of market; never let a candidate immediately choke the trade.
- Strategy engines can use different stop parameters, but cannot bypass monotonicity or initial risk controls.
- The shared smart_stop.py component is the current implementation base. It must be integrated into Engine 1 before that engine is considered complete, and integration tests must cover long/short, volatility expansion/contraction, structure, pullbacks, profit lock, and stop triggering.

## Not yet performance-validated
The shared component has code and unit tests, but until it is wired into a completed strategy and tested on stored historical data with realistic execution assumptions, it is not considered validated for profitability.
