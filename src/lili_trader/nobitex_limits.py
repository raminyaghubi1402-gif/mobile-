"""Conservative public spot-market limits documented by Nobitex.

The official API docs currently describe SmallOrder for rial markets as
3,000,000 rials. Re-check the official source before any future live integration.
"""
NOBITEX_MINIMUM_SPOT_ORDER_IRT = 3_000_000
NOBITEX_MINIMUM_ORDER_SOURCE = (
    "https://github.com/nobitex/docs-api/blob/master/source/includes/_market_trade.md"
)
