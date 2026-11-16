"""Conservative public spot-market limits documented by Nobitex.

The official API docs currently describe SmallOrder for rial markets as
3,000,000 rials. Re-check the official source before any future live integration.
"""
NOBITEX_MINIMUM_SPOT_ORDER_IRT = 3_000_000
NOBITEX_MINIMUM_ORDER_SOURCE = (
    "https://github.com/nobitex/docs-api/blob/master/source/includes/_market_trade.md"
)

# Base-tier fee for rial spot markets (maker and taker) from Nobitex pricing.
# A higher-volume/VIP account may have a different rate.
NOBITEX_BASE_SPOT_FEE_RATE_IRT = 0.0025
NOBITEX_FEE_SOURCE = "https://nobitex.company/pricing/"
