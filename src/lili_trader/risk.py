"""Deterministic, fail-closed risk gate. This module never places or cancels orders."""
from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class RiskLimits:
    max_order_quote: float
    max_position_quote: float
    max_daily_loss_quote: float
    max_drawdown_pct: float
    max_daily_trades: int
    max_market_data_age_seconds: float = 120.0

    def validate(self) -> None:
        positive = (
            self.max_order_quote,
            self.max_position_quote,
            self.max_daily_loss_quote,
            self.max_market_data_age_seconds,
        )
        if any(not isfinite(value) or value <= 0 for value in positive):
            raise ValueError("risk limits must be finite and positive")
        if not isfinite(self.max_drawdown_pct) or not 0 < self.max_drawdown_pct <= 100:
            raise ValueError("max_drawdown_pct must be in (0, 100]")
        if isinstance(self.max_daily_trades, bool) or self.max_daily_trades < 1:
            raise ValueError("max_daily_trades must be >= 1")


@dataclass(frozen=True)
class RiskSnapshot:
    daily_loss_quote: float = 0.0
    drawdown_pct: float = 0.0
    daily_trade_count: int = 0
    open_position_quote: float = 0.0
    market_data_age_seconds: float = 0.0
    exchange_state_confirmed: bool = True
    kill_switch_active: bool = False
    cooldown_active: bool = False


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reasons: tuple[str, ...]


def evaluate_order(
    order_quote: float,
    limits: RiskLimits,
    state: RiskSnapshot,
) -> RiskDecision:
    """Check a hypothetical order against hard limits; no trading side effects."""
    limits.validate()
    reasons: list[str] = []

    numeric_values = (
        order_quote,
        state.daily_loss_quote,
        state.drawdown_pct,
        state.open_position_quote,
        state.market_data_age_seconds,
    )
    if any(not isfinite(value) for value in numeric_values):
        return RiskDecision(False, ("دادهٔ عددی نامعتبر است؛ توقف امن.",))

    if order_quote <= 0:
        reasons.append("اندازه سفارش باید مثبت باشد.")
    if order_quote > limits.max_order_quote:
        reasons.append("سفارش از سقف اندازه مجاز بیشتر است.")
    if state.open_position_quote < 0 or order_quote + state.open_position_quote > limits.max_position_quote:
        reasons.append("سقف کل موقعیت رعایت نمی‌شود.")
    if state.daily_loss_quote < 0 or state.daily_loss_quote >= limits.max_daily_loss_quote:
        reasons.append("حد ضرر روزانه فعال شده است.")
    if state.drawdown_pct < 0 or state.drawdown_pct >= limits.max_drawdown_pct:
        reasons.append("حد افت سرمایه فعال شده است.")
    if state.daily_trade_count < 0 or state.daily_trade_count >= limits.max_daily_trades:
        reasons.append("سقف تعداد معاملات روزانه پر شده است.")
    if state.market_data_age_seconds < 0 or state.market_data_age_seconds > limits.max_market_data_age_seconds:
        reasons.append("دادهٔ بازار قدیمی یا زمان آن نامعتبر است.")
    if not state.exchange_state_confirmed:
        reasons.append("وضعیت صرافی/سفارش‌ها تأیید نشده است.")
    if state.kill_switch_active:
        reasons.append("کلید توقف اضطراری فعال است.")
    if state.cooldown_active:
        reasons.append("سیستم در دورهٔ توقف موقت است.")

    return RiskDecision(not reasons, tuple(reasons))
