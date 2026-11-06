"""Isolated-margin paper simulator. No broker/API/live-order functionality."""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite


class LeverageError(ValueError):
    """Invalid leveraged-paper trade."""


@dataclass(frozen=True)
class LeveragedPosition:
    side: str
    collateral: float
    leverage: float
    entry_price: float
    quantity: float
    maintenance_margin_rate: float
    liquidation_price: float
    fee_paid: float


@dataclass(frozen=True)
class PositionMark:
    mark_price: float
    unrealized_pnl: float
    equity: float
    maintenance_margin: float
    liquidated: bool


def open_isolated_position(
    side: str,
    collateral: float,
    leverage: float,
    entry_price: float,
    maintenance_margin_rate: float = 0.005,
    fee_rate: float = 0.0006,
    max_leverage: float = 3.0,
) -> LeveragedPosition:
    """Create a simulated isolated position with conservative fee reserve.

    This is an educational approximation, not an exchange liquidation formula.
    Funding, slippage, gaps, tiered maintenance margin and exchange rules are
    not modeled. It must never be used to submit a real order.
    """
    values = (collateral, leverage, entry_price, maintenance_margin_rate,
              fee_rate, max_leverage)
    if any(not isfinite(x) for x in values):
        raise LeverageError("همهٔ ورودی‌ها باید عدد معتبر باشند.")
    if side not in {"long", "short"}:
        raise LeverageError("جهت فقط long یا short است.")
    if collateral <= 0 or entry_price <= 0:
        raise LeverageError("وثیقه و قیمت باید مثبت باشند.")
    if leverage < 1 or leverage > max_leverage:
        raise LeverageError(f"اهرم باید بین ۱ و {max_leverage:g} باشد.")
    if not 0 < maintenance_margin_rate < 1:
        raise LeverageError("نرخ مارجین نگهداری باید بین صفر و یک باشد.")
    if not 0 <= fee_rate < 1:
        raise LeverageError("نرخ کارمزد نامعتبر است.")

    notional = collateral * leverage
    fee = notional * fee_rate
    # Reserve opening fee from isolated collateral; reject if it consumes
    # collateral required for maintenance margin.
    usable_collateral = collateral - fee
    if usable_collateral <= notional * maintenance_margin_rate:
        raise LeverageError("وثیقه پس از کارمزد برای مارجین نگهداری کافی نیست.")
    quantity = notional / entry_price

    # Approximate liquidation threshold where equity = maintenance margin.
    # Long: C + Q(P-E) = Q*P*m -> P = (Q*E-C)/(Q*(1-m))
    # Short: C + Q(E-P) = Q*P*m -> P = (C+Q*E)/(Q*(1+m))
    if side == "long":
        liquidation = (usable_collateral + quantity * entry_price) / (quantity * (1 + maintenance_margin_rate))
        # Conservative long-side formula solving equity against maintenance:
        liquidation = (quantity * entry_price - usable_collateral) / (quantity * (1 - maintenance_margin_rate))
    else:
        liquidation = (usable_collateral + quantity * entry_price) / (quantity * (1 + maintenance_margin_rate))
    if not isfinite(liquidation) or liquidation <= 0:
        raise LeverageError("قیمت لیکوییدیشن محاسبه‌پذیر نیست.")

    return LeveragedPosition(side, usable_collateral, leverage, entry_price,
                             quantity, maintenance_margin_rate, liquidation, fee)


def mark_position(position: LeveragedPosition, mark_price: float) -> PositionMark:
    if not isfinite(mark_price) or mark_price <= 0:
        raise LeverageError("قیمت لحظه‌ای باید مثبت و معتبر باشد.")
    direction = 1.0 if position.side == "long" else -1.0
    pnl = direction * position.quantity * (mark_price - position.entry_price)
    equity = position.collateral + pnl
    maintenance = position.quantity * mark_price * position.maintenance_margin_rate
    liquidated = equity <= maintenance or (
        position.side == "long" and mark_price <= position.liquidation_price
    ) or (
        position.side == "short" and mark_price >= position.liquidation_price
    )
    return PositionMark(mark_price, pnl, equity, maintenance, liquidated)
