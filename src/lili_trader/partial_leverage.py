"""Staged partial take-profit for managed isolated-leverage paper positions only."""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from .leverage_paper import LeveragedPosition, PositionMark, mark_position
from .managed_leverage import ManagedPositionState
from .smart_stop import SmartStopState, SmartStopUpdate, update_smart_stop


class PartialExitError(ValueError):
    """Invalid staged-exit configuration or market input."""


@dataclass(frozen=True)
class TakeProfitStage:
    target_r: float
    close_fraction_of_initial: float


DEFAULT_TAKE_PROFIT_STAGES = (
    TakeProfitStage(1.0, 0.25),
    TakeProfitStage(2.0, 0.25),
)


@dataclass(frozen=True)
class PartialExitState:
    original_position: LeveragedPosition
    stop: SmartStopState
    stages: tuple[TakeProfitStage, ...]
    remaining_quantity: float
    remaining_collateral: float
    remaining_entry_fee: float
    realized_gross_pnl: float = 0.0
    exit_fees_paid: float = 0.0
    completed_stages: tuple[int, ...] = ()
    status: str = "open"
    last_mark: PositionMark | None = None
    final_net_pnl: float | None = None
    close_reason: str | None = None


@dataclass(frozen=True)
class PartialExitUpdate:
    state: PartialExitState
    stop_update: SmartStopUpdate | None
    event: str
    fills: tuple[tuple[float, float], ...] = ()


def start_partial_exit_plan(
    managed: ManagedPositionState,
    *,
    stages: tuple[TakeProfitStage, ...] = DEFAULT_TAKE_PROFIT_STAGES,
) -> PartialExitState:
    """Start two-stage partial exits from an already risk-approved paper position.

    Fractions are percentages of the original quantity, not of the remainder.
    The default closes 25% at +1R and 25% at +2R, leaving 50% as a runner.
    """
    if managed.status != "open":
        raise PartialExitError("فقط موقعیت باز قابل مدیریت است.")
    if not stages:
        raise PartialExitError("حداقل یک مرحلهٔ حد سود لازم است.")
    last_target = 0.0
    total_fraction = 0.0
    for stage in stages:
        if (not isfinite(stage.target_r) or not isfinite(stage.close_fraction_of_initial)
                or stage.target_r <= last_target
                or not 0 < stage.close_fraction_of_initial <= 1):
            raise PartialExitError("مراحل حد سود باید صعودی و درصدها معتبر باشند.")
        last_target = stage.target_r
        total_fraction += stage.close_fraction_of_initial
    if total_fraction >= 1:
        raise PartialExitError("مجموع برداشت پله‌ای باید کمتر از ۱۰۰٪ باشد.")

    p = managed.position
    if (not isfinite(p.quantity) or p.quantity <= 0
            or not isfinite(managed.entry_fee) or managed.entry_fee < 0):
        raise PartialExitError("موقعیت پایه نامعتبر است.")
    return PartialExitState(
        original_position=p, stop=managed.stop, stages=stages,
        remaining_quantity=p.quantity, remaining_collateral=p.collateral,
        remaining_entry_fee=managed.entry_fee,
    )


def update_partial_exit(
    state: PartialExitState,
    market_price: float,
    *,
    close_fee_rate: float = 0.0006,
) -> PartialExitUpdate:
    """Process one price observation; no broker calls or live orders.

    Partial fills are conservatively booked at the target level even if price
    gaps beyond it. Stop exits use the observed market price. Liquidation is
    checked first on the remaining position. Realized profit is not added back
    to remaining collateral, keeping the liquidation estimate conservative.
    """
    if state.status != "open":
        return PartialExitUpdate(state, None, "already_closed")
    if not isfinite(market_price) or market_price <= 0:
        raise PartialExitError("قیمت بازار نامعتبر است.")
    if not isfinite(close_fee_rate) or not 0 <= close_fee_rate < 1:
        raise PartialExitError("نرخ کارمزد خروج نامعتبر است.")

    p = state.original_position
    remaining_position = LeveragedPosition(
        side=p.side,
        collateral=state.remaining_collateral,
        leverage=p.leverage,
        entry_price=p.entry_price,
        quantity=state.remaining_quantity,
        maintenance_margin_rate=p.maintenance_margin_rate,
        liquidation_price=p.liquidation_price,
        fee_paid=state.remaining_entry_fee,
    )
    mark = mark_position(remaining_position, market_price)

    def finish(reason: str, fill_price: float, mark_value: PositionMark,
               realized: float, exit_fees: float, entry_fees: float,
               stop: SmartStopState, stop_update: SmartStopUpdate | None,
               fills: tuple[tuple[float, float], ...]) -> PartialExitUpdate:
        remainder_pnl = (1.0 if p.side == "long" else -1.0) * state.remaining_quantity * (fill_price - p.entry_price)
        final_exit_fee = state.remaining_quantity * fill_price * close_fee_rate
        total_gross = realized + remainder_pnl
        total_fees = exit_fees + final_exit_fee + entry_fees
        closed = PartialExitState(
            original_position=p, stop=stop, stages=state.stages, remaining_quantity=0.0,
            remaining_collateral=0.0, remaining_entry_fee=0.0,
            realized_gross_pnl=total_gross, exit_fees_paid=exit_fees + final_exit_fee,
            completed_stages=state.completed_stages, status="liquidated" if reason == "liquidation" else "closed",
            last_mark=mark_value, final_net_pnl=total_gross - total_fees,
            close_reason=reason,
        )
        return PartialExitUpdate(closed, stop_update, reason, fills)

    if mark.liquidated:
        return finish(
            "liquidation", market_price, mark, state.realized_gross_pnl,
            state.exit_fees_paid, state.remaining_entry_fee, state.stop, None, (),
        )

    risk = abs(p.entry_price - state.stop.initial_stop)
    if not isfinite(risk) or risk <= 0:
        raise PartialExitError("ریسک اولیهٔ حد ضرر نامعتبر است.")

    new_remaining = state.remaining_quantity
    new_collateral = state.remaining_collateral
    new_entry_fee = state.remaining_entry_fee
    realized = state.realized_gross_pnl
    fees = state.exit_fees_paid
    completed = list(state.completed_stages)
    fills: list[tuple[float, float]] = []

    for index, stage in enumerate(state.stages):
        if index in completed:
            continue
        target = p.entry_price + stage.target_r * risk if p.side == "long" else p.entry_price - stage.target_r * risk
        reached = market_price >= target if p.side == "long" else market_price <= target
        if not reached:
            continue
        qty = min(p.quantity * stage.close_fraction_of_initial, new_remaining)
        if qty <= 0:
            completed.append(index)
            continue
        # Target price, not the better observed gap price: conservative fill.
        pnl = (1.0 if p.side == "long" else -1.0) * qty * (target - p.entry_price)
        fee = qty * target * close_fee_rate
        entry_fee_alloc = new_entry_fee * (qty / new_remaining)
        realized += pnl
        fees += fee
        new_entry_fee -= entry_fee_alloc
        new_remaining -= qty
        new_collateral *= (new_remaining / (new_remaining + qty))
        completed.append(index)
        fills.append((target, qty))

    # Build the updated position mark with reduced quantity/collateral.
    next_position = LeveragedPosition(
        side=p.side, collateral=new_collateral, leverage=p.leverage,
        entry_price=p.entry_price, quantity=new_remaining,
        maintenance_margin_rate=p.maintenance_margin_rate,
        liquidation_price=p.liquidation_price, fee_paid=new_entry_fee,
    )
    next_mark = mark_position(next_position, market_price) if new_remaining > 0 else mark
    stop_update = update_smart_stop(state.stop, market_price)
    if stop_update.stop_triggered and new_remaining > 0:
        next_state = PartialExitState(
            original_position=p, stop=stop_update.state, stages=state.stages,
            remaining_quantity=new_remaining, remaining_collateral=new_collateral,
            remaining_entry_fee=new_entry_fee, realized_gross_pnl=realized,
            exit_fees_paid=fees, completed_stages=tuple(completed),
        )
        # Close the runner at the observed price, preserving all partial fills.
        rem_pnl = (1.0 if p.side == "long" else -1.0) * new_remaining * (market_price - p.entry_price)
        rem_fee = new_remaining * market_price * close_fee_rate
        total_gross = realized + rem_pnl
        total_fees = fees + new_entry_fee + rem_fee
        closed = PartialExitState(
            original_position=p, stop=stop_update.state, stages=state.stages, remaining_quantity=0.0,
            remaining_collateral=0.0, remaining_entry_fee=0.0,
            realized_gross_pnl=total_gross, exit_fees_paid=fees + rem_fee,
            completed_stages=tuple(completed), status="closed",
            last_mark=next_mark, final_net_pnl=total_gross - total_fees,
            close_reason="smart_stop",
        )
        return PartialExitUpdate(closed, stop_update, "stopped", tuple(fills))

    updated = PartialExitState(
        original_position=p, stop=stop_update.state, stages=state.stages,
        remaining_quantity=new_remaining, remaining_collateral=new_collateral,
        remaining_entry_fee=new_entry_fee, realized_gross_pnl=realized,
        exit_fees_paid=fees, completed_stages=tuple(completed),
        last_mark=next_mark,
    )
    event = "partial_take_profit" if fills else "open"
    return PartialExitUpdate(updated, stop_update, event, tuple(fills))
