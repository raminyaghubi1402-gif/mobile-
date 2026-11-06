"""Risk-gated isolated leverage simulation; never sends exchange orders."""
from dataclasses import dataclass
from .leverage_paper import LeveragedPosition, open_isolated_position
from .risk import RiskLimits, RiskSnapshot, evaluate_order

@dataclass(frozen=True)
class LeveragedOpenDecision:
    allowed: bool
    reasons: tuple[str, ...]
    position: LeveragedPosition | None = None

def open_risk_checked_paper_position(side: str, collateral: float, leverage: float, entry_price: float, limits: RiskLimits, state: RiskSnapshot, *, maintenance_margin_rate: float = 0.005, fee_rate: float = 0.0006, max_leverage: float = 3.0) -> LeveragedOpenDecision:
    """Open a simulated position only if the independent risk gate permits it.

    Risk limits are applied to full leveraged notional, not collateral alone.
    This function has no network or exchange-order capability.
    """
    try:
        if collateral <= 0 or leverage < 1:
            return LeveragedOpenDecision(False, ("وثیقه و اهرم نامعتبر است.",))
        notional = collateral * leverage
        decision = evaluate_order(notional, limits, state)
        if not decision.allowed:
            return LeveragedOpenDecision(False, decision.reasons)
        position = open_isolated_position(side, collateral, leverage, entry_price, maintenance_margin_rate=maintenance_margin_rate, fee_rate=fee_rate, max_leverage=max_leverage)
        return LeveragedOpenDecision(True, (), position)
    except (TypeError, ValueError, OverflowError):
        return LeveragedOpenDecision(False, ("ورودی نامعتبر است؛ توقف امن.",))
