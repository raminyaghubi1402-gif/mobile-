"""Offline paper-trading ledger. No broker, network, or live-order capability."""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite


class PaperTradingError(ValueError):
    """Invalid simulated trade or insufficient simulated funds."""


@dataclass(frozen=True)
class PaperFill:
    order_id: str
    side: str
    quantity: float
    price: float
    fee_quote: float
    cash_after: float
    position_after: float


class PaperAccount:
    """Small deterministic spot-only simulator; state exists only in memory."""

    def __init__(self, starting_cash: float, fee_rate: float = 0.001) -> None:
        if not isfinite(starting_cash) or starting_cash <= 0:
            raise PaperTradingError("starting_cash must be finite and positive")
        if not isfinite(fee_rate) or not 0 <= fee_rate < 1:
            raise PaperTradingError("fee_rate must be in [0, 1)")
        self.starting_cash = float(starting_cash)
        self.cash = float(starting_cash)
        self.fee_rate = float(fee_rate)
        self.position = 0.0
        self._fills: dict[str, PaperFill] = {}

    @property
    def fills(self) -> tuple[PaperFill, ...]:
        return tuple(self._fills.values())

    def execute(self, order_id: str, side: str, quantity: float, price: float) -> PaperFill:
        """Simulate a market fill. Repeated order IDs return the original fill."""
        if not isinstance(order_id, str) or not order_id.strip():
            raise PaperTradingError("order_id is required")
        if order_id in self._fills:
            return self._fills[order_id]
        if side not in {"buy", "sell"}:
            raise PaperTradingError("side must be buy or sell")
        if any(not isfinite(v) or v <= 0 for v in (quantity, price)):
            raise PaperTradingError("quantity and price must be finite and positive")

        gross = quantity * price
        if not isfinite(gross):
            raise PaperTradingError("trade value overflow")
        fee = gross * self.fee_rate
        if side == "buy":
            total = gross + fee
            if total > self.cash:
                raise PaperTradingError("insufficient simulated cash")
            next_cash = self.cash - total
            next_position = self.position + quantity
        else:
            if quantity > self.position:
                raise PaperTradingError("cannot sell more than simulated position")
            next_cash = self.cash + gross - fee
            next_position = self.position - quantity

        # Commit state only after all validation succeeds.
        fill = PaperFill(order_id, side, quantity, price, fee, next_cash, next_position)
        self.cash, self.position = next_cash, next_position
        self._fills[order_id] = fill
        return fill

    def equity(self, mark_price: float) -> float:
        if not isfinite(mark_price) or mark_price <= 0:
            raise PaperTradingError("mark_price must be finite and positive")
        return self.cash + self.position * mark_price
