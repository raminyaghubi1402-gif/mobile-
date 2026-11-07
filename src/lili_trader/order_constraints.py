"""Conservative validation for exchange order size/precision constraints.

This module is intentionally exchange-agnostic. Do not invent market rules:
load these values from verified market metadata or config. Missing rules should
prevent a live order; this helper is not wired to live execution.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_DOWN


def _decimal(value: Decimal | str | int | float) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("value must be a valid decimal") from exc
    if not result.is_finite():
        raise ValueError("value must be finite")
    return result


@dataclass(frozen=True)
class MarketOrderRules:
    min_quantity: Decimal
    quantity_step: Decimal
    min_notional: Decimal

    def __post_init__(self) -> None:
        # Normalize user/config inputs once; float conversion goes through str
        # to avoid importing binary floating-point artifacts into Decimal.
        values = tuple(_decimal(value) for value in (
            self.min_quantity, self.quantity_step, self.min_notional
        ))
        if any(value <= 0 for value in values):
            raise ValueError("market constraints must be finite and positive")
        for name, value in zip(
            ("min_quantity", "quantity_step", "min_notional"), values
        ):
            object.__setattr__(self, name, value)


@dataclass(frozen=True)
class OrderSizeCheck:
    accepted: bool
    quantity: Decimal
    notional: Decimal
    reason: str


def validate_order_size(
    quantity: Decimal | str | int | float,
    price: Decimal | str | int | float,
    rules: MarketOrderRules | None,
) -> OrderSizeCheck:
    """Round quantity down to step and reject unknown or infeasible rules."""
    qty, px = _decimal(quantity), _decimal(price)
    if qty <= 0 or px <= 0:
        return OrderSizeCheck(False, Decimal("0"), Decimal("0"), "invalid_quantity_or_price")
    if rules is None:
        return OrderSizeCheck(False, Decimal("0"), Decimal("0"), "market_rules_unknown")

    stepped = (qty / rules.quantity_step).to_integral_value(rounding=ROUND_DOWN) * rules.quantity_step
    notional = stepped * px
    if stepped < rules.min_quantity:
        return OrderSizeCheck(False, stepped, notional, "below_minimum_quantity")
    if notional < rules.min_notional:
        return OrderSizeCheck(False, stepped, notional, "below_minimum_notional")
    return OrderSizeCheck(True, stepped, notional, "accepted")
