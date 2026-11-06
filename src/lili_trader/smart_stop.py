"""Monotonic, staged profit-locking stop manager; simulation only."""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite


class SmartStopError(ValueError):
    pass


@dataclass(frozen=True)
class StopStage:
    activate_r: float
    lock_r: float


@dataclass(frozen=True)
class SmartStopState:
    side: str
    entry_price: float
    initial_stop: float
    current_stop: float
    initial_atr: float
    favorable_extreme: float
    stage_index: int = -1


@dataclass(frozen=True)
class SmartStopUpdate:
    state: SmartStopState
    stop_changed: bool
    stop_triggered: bool
    locked_profit_r: float
    reason: str


DEFAULT_STAGES = (
    StopStage(1.0, 0.0),   # At +1R, protect break-even (before fees).
    StopStage(2.0, 1.0),   # At +2R, protect +1R.
    StopStage(3.0, 2.0),   # At +3R, protect +2R.
    StopStage(4.0, 3.0),   # At +4R, protect +3R.
)


def create_smart_stop(
    side: str,
    entry_price: float,
    initial_stop: float,
    initial_atr: float,
) -> SmartStopState:
    values = (entry_price, initial_stop, initial_atr)
    if side not in {"long", "short"}:
        raise SmartStopError("جهت معامله باید long یا short باشد.")
    if any(not isfinite(v) or v <= 0 for v in values):
        raise SmartStopError("قیمت و ATR باید مثبت و معتبر باشند.")
    if side == "long" and initial_stop >= entry_price:
        raise SmartStopError("حد ضرر اولیهٔ خرید باید پایین‌تر از ورود باشد.")
    if side == "short" and initial_stop <= entry_price:
        raise SmartStopError("حد ضرر اولیهٔ فروش باید بالاتر از ورود باشد.")
    return SmartStopState(
        side=side, entry_price=entry_price, initial_stop=initial_stop,
        current_stop=initial_stop, initial_atr=initial_atr,
        favorable_extreme=entry_price, stage_index=-1,
    )


def update_smart_stop(
    state: SmartStopState,
    market_price: float,
    *,
    stages: tuple[StopStage, ...] = DEFAULT_STAGES,
    atr_trailing_multiple: float = 2.5,
) -> SmartStopUpdate:
    """Tighten only; never loosen, even if volatility or inputs change.

    ATR is frozen at entry in state.initial_atr. Profit stages use R, where
    1R is the original entry-to-stop distance. The trailing candidate follows
    the best favorable price but is clamped so it cannot move backwards.
    """
    if not isfinite(market_price) or market_price <= 0:
        raise SmartStopError("قیمت بازار نامعتبر است.")
    if not isfinite(atr_trailing_multiple) or atr_trailing_multiple <= 0:
        raise SmartStopError("ضریب ATR باید مثبت و معتبر باشد.")
    previous = state.current_stop
    risk = abs(state.entry_price - state.initial_stop)
    if risk <= 0:
        raise SmartStopError("فاصلهٔ حد ضرر اولیه نامعتبر است.")
    last_activation = -1.0
    last_lock = -1.0
    for stage in stages:
        if (not isfinite(stage.activate_r) or not isfinite(stage.lock_r)
                or stage.activate_r <= 0 or stage.lock_r < 0
                or stage.lock_r >= stage.activate_r
                or stage.activate_r <= last_activation
                or stage.lock_r < last_lock):
            raise SmartStopError("مراحل قفل سود باید مرتب و معتبر باشند.")
        last_activation, last_lock = stage.activate_r, stage.lock_r

    if state.side == "long":
        extreme = max(state.favorable_extreme, market_price)
        favorable_r = (extreme - state.entry_price) / risk
        candidates = [previous]
        stage_index = state.stage_index
        for i, stage in enumerate(stages):
            if favorable_r >= stage.activate_r:
                candidates.append(state.entry_price + stage.lock_r * risk)
                stage_index = max(stage_index, i)
        candidates.append(extreme - state.initial_atr * atr_trailing_multiple)
        # The max guarantees a long stop can never decrease.
        new_stop = max(candidates)
        triggered = market_price <= new_stop
        locked_r = (new_stop - state.entry_price) / risk
    elif state.side == "short":
        extreme = min(state.favorable_extreme, market_price)
        favorable_r = (state.entry_price - extreme) / risk
        candidates = [previous]
        stage_index = state.stage_index
        for i, stage in enumerate(stages):
            if favorable_r >= stage.activate_r:
                candidates.append(state.entry_price - stage.lock_r * risk)
                stage_index = max(stage_index, i)
        candidates.append(extreme + state.initial_atr * atr_trailing_multiple)
        # The min guarantees a short stop can never increase.
        new_stop = min(candidates)
        triggered = market_price >= new_stop
        locked_r = (state.entry_price - new_stop) / risk
    else:
        raise SmartStopError("جهت موقعیت نامعتبر است.")

    updated = SmartStopState(
        side=state.side, entry_price=state.entry_price,
        initial_stop=state.initial_stop, current_stop=new_stop,
        initial_atr=state.initial_atr, favorable_extreme=extreme,
        stage_index=stage_index,
    )
    changed = new_stop > previous if state.side == "long" else new_stop < previous
    return SmartStopUpdate(
        state=updated, stop_changed=changed, stop_triggered=triggered,
        locked_profit_r=locked_r,
        reason="حد ضرر فقط در جهت کاهش ریسک به‌روزرسانی شد.",
    )
