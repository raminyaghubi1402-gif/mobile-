"""ATR smart-stop management for isolated leveraged paper positions only.

No network, broker, exchange credentials, or live-order capability.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from collections.abc import Sequence

from .leverage_paper import LeveragedPosition, PositionMark, mark_position
from .smart_stop import SmartStopState, SmartStopUpdate, update_smart_stop
from .volatility_stop import VolatilityStopError, build_volatility_stop


class ManagedPositionError(ValueError):
    """Invalid or unsafe managed paper position."""


@dataclass(frozen=True)
class ManagedPositionState:
    position: LeveragedPosition
    stop: SmartStopState
    status: str = "open"
    last_mark: PositionMark | None = None
    exit_price: float | None = None
    gross_pnl: float | None = None
    entry_fee: float = 0.0
    exit_fee: float = 0.0
    net_pnl_after_fees: float | None = None
    close_reason: str | None = None


@dataclass(frozen=True)
class ManagedPositionUpdate:
    state: ManagedPositionState
    stop_update: SmartStopUpdate | None
    event: str


def open_managed_leverage_paper_position(
    position: LeveragedPosition,
    candles: Sequence[dict[str, float]],
    *,
    risk_budget_quote: float,
    atr_period: int = 14,
    stop_atr_multiple: float = 2.0,
    max_stop_distance_pct: float = 10.0,
    liquidation_buffer_pct: float = 2.0,
) -> ManagedPositionState:
    """Attach an ATR stop only if size and liquidation clearance pass.

    Risk uses full position quantity × stop distance, not collateral. It
    excludes gaps, slippage, funding and changing exchange margin rules.
    """
    if not isinstance(position, LeveragedPosition):
        raise ManagedPositionError("موقعیت اهرمی معتبر نیست.")
    if not isfinite(risk_budget_quote) or risk_budget_quote <= 0:
        raise ManagedPositionError("بودجهٔ ریسک باید مثبت و معتبر باشد.")
    if not isfinite(liquidation_buffer_pct) or not 0 < liquidation_buffer_pct < 50:
        raise ManagedPositionError("حاشیهٔ امن لیکوییدیشن باید بین صفر و ۵۰ درصد باشد.")

    try:
        plan = build_volatility_stop(
            position.side,
            position.entry_price,
            candles,
            atr_period=atr_period,
            stop_atr_multiple=stop_atr_multiple,
            risk_budget_quote=risk_budget_quote,
            max_position_quote=position.quantity * position.entry_price,
            max_stop_distance_pct=max_stop_distance_pct,
        )
    except (VolatilityStopError, TypeError, ValueError) as exc:
        raise ManagedPositionError(str(exc)) from exc

    # Never silently shrink an already-open position: reject if its full
    # leveraged notional exceeds the ATR-based risk budget.
    actual_risk = position.quantity * plan.stop_distance
    if not isfinite(actual_risk) or actual_risk > risk_budget_quote:
        raise ManagedPositionError("ریسک موقعیت اهرمی از بودجهٔ مجاز بیشتر است؛ موقعیت پذیرفته نشد.")

    buffer = liquidation_buffer_pct / 100.0
    if position.side == "long":
        safe = plan.state.initial_stop >= position.liquidation_price * (1 + buffer)
    else:
        safe = plan.state.initial_stop <= position.liquidation_price * (1 - buffer)
    if not safe:
        raise ManagedPositionError("حد ضرر به قیمت لیکوییدیشن بیش از حد نزدیک است؛ موقعیت رد شد.")

    return ManagedPositionState(
        position=position,
        stop=plan.state,
        entry_fee=position.fee_paid,
    )


def update_managed_leverage_paper_position(
    state: ManagedPositionState,
    market_price: float,
    *,
    close_fee_rate: float = 0.0006,
) -> ManagedPositionUpdate:
    """Mark an open position; liquidation takes precedence over the stop.

    A stop gap is filled at the supplied market price (not the stop price),
    making the simulation less optimistic. Repeated calls after closure are
    idempotent and do not charge another fee.
    """
    if state.status != "open":
        return ManagedPositionUpdate(state, None, "already_closed")
    if not isfinite(close_fee_rate) or not 0 <= close_fee_rate < 1:
        raise ManagedPositionError("نرخ کارمزد خروج نامعتبر است.")

    try:
        mark = mark_position(state.position, market_price)
    except (TypeError, ValueError) as exc:
        raise ManagedPositionError(str(exc)) from exc

    if mark.liquidated:
        return ManagedPositionUpdate(
            ManagedPositionState(
                position=state.position, stop=state.stop, status="liquidated",
                last_mark=mark, exit_price=market_price,
                gross_pnl=mark.unrealized_pnl, entry_fee=state.entry_fee,
                exit_fee=0.0,
                net_pnl_after_fees=mark.unrealized_pnl - state.entry_fee,
                close_reason="liquidation",
            ),
            None,
            "liquidated",
        )

    try:
        stop_update = update_smart_stop(state.stop, market_price)
    except (TypeError, ValueError) as exc:
        raise ManagedPositionError(str(exc)) from exc

    if not stop_update.stop_triggered:
        return ManagedPositionUpdate(
            ManagedPositionState(
                position=state.position, stop=stop_update.state,
                status="open", last_mark=mark, entry_fee=state.entry_fee,
            ),
            stop_update,
            "open",
        )

    # Conservative gap handling: assume fill at observed market price.
    exit_fee = state.position.quantity * market_price * close_fee_rate
    net = mark.unrealized_pnl - state.entry_fee - exit_fee
    closed = ManagedPositionState(
        position=state.position, stop=stop_update.state, status="closed",
        last_mark=mark, exit_price=market_price,
        gross_pnl=mark.unrealized_pnl, entry_fee=state.entry_fee,
        exit_fee=exit_fee, net_pnl_after_fees=net,
        close_reason="smart_stop",
    )
    return ManagedPositionUpdate(closed, stop_update, "stopped")
