"""Build ATR-sized smart stops and risk-bounded paper position sizes."""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from collections.abc import Sequence

from .smart_stop import SmartStopState, create_smart_stop
from .volatility import atr


class VolatilityStopError(ValueError):
    """Invalid volatility-based stop or sizing input."""


@dataclass(frozen=True)
class VolatilityStopPlan:
    state: SmartStopState
    atr_value: float
    stop_distance: float
    quantity: float
    notional_quote: float
    risk_quote: float


def build_volatility_stop(
    side: str,
    entry_price: float,
    candles: Sequence[dict[str, float]],
    *,
    atr_period: int = 14,
    stop_atr_multiple: float = 2.0,
    risk_budget_quote: float,
    max_position_quote: float,
    max_stop_distance_pct: float = 10.0,
) -> VolatilityStopPlan:
    """Create an ATR stop and size quantity so stop-distance risk stays bounded.

    This is a planning helper for analysis/paper trading only. It assumes a
    stop fill at the stop price and therefore excludes fees, gaps and slippage
    from risk_quote; callers must stress-test those separately.
    """
    values = (entry_price, stop_atr_multiple, risk_budget_quote,
              max_position_quote, max_stop_distance_pct)
    if any(not isfinite(value) or value <= 0 for value in values):
        raise VolatilityStopError("ورودی‌ها باید مثبت و معتبر باشند.")
    if side not in {"long", "short"}:
        raise VolatilityStopError("جهت معامله نامعتبر است.")

    try:
        atr_value = atr(candles, atr_period)
    except ValueError as exc:
        raise VolatilityStopError(str(exc)) from exc
    distance = atr_value * stop_atr_multiple
    distance_pct = distance / entry_price * 100
    if not isfinite(distance) or distance <= 0:
        raise VolatilityStopError("فاصلهٔ حد ضرر قابل محاسبه نیست.")
    if distance_pct > max_stop_distance_pct:
        raise VolatilityStopError("نوسان بیش از حد مجاز است؛ ورود جدید متوقف شد.")

    initial_stop = entry_price - distance if side == "long" else entry_price + distance
    state = create_smart_stop(side, entry_price, initial_stop, atr_value)

    # Position size is reduced as volatility widens the stop. It is also
    # capped by a quote-notional ceiling. No leverage is applied here.
    quantity_by_risk = risk_budget_quote / distance
    quantity_by_notional = max_position_quote / entry_price
    quantity = min(quantity_by_risk, quantity_by_notional)
    if not isfinite(quantity) or quantity <= 0:
        raise VolatilityStopError("اندازهٔ موقعیت معتبر نیست.")
    notional = quantity * entry_price
    risk_quote = quantity * distance
    return VolatilityStopPlan(
        state=state,
        atr_value=atr_value,
        stop_distance=distance,
        quantity=quantity,
        notional_quote=notional,
        risk_quote=risk_quote,
    )
