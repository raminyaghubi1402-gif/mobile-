"""Historical three-timeframe price-action backtester; no exchange execution.

Signals are evaluated only after the 5m candle closes. Higher-timeframe candles
are included only after their own close timestamp. Entries occur at the next
5m candle open. This is a conservative research simulator, not a live strategy.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from collections.abc import Sequence

from .price_action_trend import classify_price_action_trend
from .timeframe_gate import evaluate_timeframe_gate
from .smart_stop import create_smart_stop, update_smart_stop


@dataclass(frozen=True)
class PriceActionTrade:
    direction: str
    entry_timestamp: int
    exit_timestamp: int
    entry_price: float
    exit_price: float
    quantity: float
    gross_pnl: float
    fees: float
    net_pnl: float
    exit_reason: str


@dataclass(frozen=True)
class ThreeTimeframeBacktestResult:
    starting_cash: float
    ending_equity: float
    net_profit: float
    return_pct: float
    max_drawdown_pct: float
    win_rate_pct: float
    profit_factor: float | None
    trade_count: int
    buy_count: int
    sell_count: int
    trades: tuple[PriceActionTrade, ...]
    equity_curve: tuple[float, ...]
    gate_counts: tuple[tuple[str, int], ...]
    warnings: tuple[str, ...]


def _number(candle, field: str) -> float:
    raw = candle.get(field) if isinstance(candle, dict) else getattr(candle, field)
    value = float(raw)
    if not isfinite(value):
        raise ValueError("candle contains non-finite values")
    return value


def _timestamp(candle) -> int:
    raw = candle.get("timestamp") if isinstance(candle, dict) else getattr(candle, "timestamp")
    value = int(raw)
    if value < 0:
        raise ValueError("candle timestamp must be non-negative")
    return value


def _validated(candles: Sequence, timeframe_seconds: int, name: str) -> list[dict]:
    if not isinstance(candles, Sequence) or len(candles) < 10:
        raise ValueError(f"{name} needs at least 10 candles")
    rows = []
    previous = -1
    for candle in candles:
        ts = _timestamp(candle)
        o, h, low, close = (_number(candle, key) for key in ("open", "high", "low", "close"))
        if ts <= previous or any(v <= 0 for v in (o, h, low, close)):
            raise ValueError(f"{name} candles must be positive and chronological")
        if low > min(o, close) or h < max(o, close) or low > h:
            raise ValueError(f"{name} contains inconsistent OHLC")
        rows.append({"timestamp": ts, "open": o, "high": h, "low": low, "close": close})
        previous = ts
    # Reject duplicate/gapped bars rather than silently pretending missing data is continuous.
    if any(rows[i]["timestamp"] - rows[i - 1]["timestamp"] != timeframe_seconds
           for i in range(1, len(rows))):
        raise ValueError(f"{name} has missing or irregular candle intervals")
    return rows


def _atr(candles: Sequence[dict], period: int = 14) -> float:
    if len(candles) < period:
        raise ValueError("not enough closed 15m candles for ATR")
    ranges = []
    for index, candle in enumerate(candles):
        previous_close = candles[index - 1]["close"] if index else candle["close"]
        ranges.append(max(candle["high"] - candle["low"],
                          abs(candle["high"] - previous_close),
                          abs(candle["low"] - previous_close)))
    value = sum(ranges[:period]) / period
    for current in ranges[period:]:
        value = ((period - 1) * value + current) / period
    if not isfinite(value) or value <= 0:
        raise ValueError("ATR is invalid")
    return value


def run_three_timeframe_backtest(
    hourly_candles: Sequence,
    fifteen_minute_candles: Sequence,
    five_minute_candles: Sequence,
    *,
    starting_cash: float = 1_000_000.0,
    risk_fraction: float = 0.005,
    stop_atr_multiple: float = 2.0,
    reward_r_multiple: float = 2.0,
    fee_rate: float = 0.001,
    slippage_rate: float = 0.001,
    trend_lookback: int = 120,
    atr_period: int = 14,
) -> ThreeTimeframeBacktestResult:
    """Backtest 1h direction / 15m setup / 5m trigger with no look-ahead.

    Long and short are simulated without leverage; notional is capped at current
    equity. The 1h direction must be up/down, 15m must resume that direction,
    and 5m must confirm it. Range/unknown and countertrend states do not trade.
    """
    if not isfinite(starting_cash) or starting_cash <= 0:
        raise ValueError("starting_cash must be finite and positive")
    if not isfinite(risk_fraction) or not 0 < risk_fraction <= 0.02:
        raise ValueError("risk_fraction must be > 0 and <= 2%")
    if not isfinite(stop_atr_multiple) or not 0.1 <= stop_atr_multiple <= 10:
        raise ValueError("stop_atr_multiple outside safe research bounds")
    if not isfinite(reward_r_multiple) or not 0.25 <= reward_r_multiple <= 10:
        raise ValueError("reward_r_multiple outside safe research bounds")
    if not isfinite(fee_rate) or not 0 <= fee_rate < 0.05:
        raise ValueError("fee_rate outside allowed bounds")
    if not isfinite(slippage_rate) or not 0 <= slippage_rate < 0.05:
        raise ValueError("slippage_rate outside allowed bounds")
    if trend_lookback < 20 or atr_period < 2:
        raise ValueError("lookback/ATR period too small")

    hourly = _validated(hourly_candles, 3600, "1h")
    fifteen = _validated(fifteen_minute_candles, 900, "15m")
    five = _validated(five_minute_candles, 300, "5m")

    cash = float(starting_cash)
    position = None
    trades: list[PriceActionTrade] = []
    curve = [cash]
    peak = cash
    max_drawdown = 0.0
    gate_counts: dict[str, int] = {}

    def finish_position(bar: dict, raw_price: float, reason: str) -> None:
        nonlocal cash, position
        pos = position
        if pos is None: return
        side = 1 if pos['direction'] == 'BUY' else -1
        fill = raw_price * (1 - slippage_rate if side == 1 else 1 + slippage_rate)
        qty = pos['remaining_quantity']
        exit_fee = fill * qty * fee_rate
        gross = (fill - pos['entry_price']) * qty * side
        cash += gross - exit_fee
        total_gross = pos['realized_gross'] + gross
        total_fees = pos['entry_fee'] + pos['exit_fees'] + exit_fee
        trades.append(PriceActionTrade(direction=pos['direction'], entry_timestamp=pos['entry_timestamp'], exit_timestamp=bar['timestamp'], entry_price=pos['entry_price'], exit_price=fill, quantity=pos['original_quantity'], gross_pnl=total_gross, fees=total_fees, net_pnl=total_gross-total_fees, exit_reason=reason))
        position = None

    def check_existing_stop(bar: dict) -> bool:
        if position is None: return False
        pos = position
        stop = pos['stop_state'].current_stop
        if pos['direction'] == 'BUY':
            if bar['open'] <= stop: finish_position(bar, bar['open'], 'stop_gap'); return True
            if bar['low'] <= stop: finish_position(bar, stop, 'smart_stop' if pos['stop_state'].stage_index >= 0 else 'stop_loss'); return True
        else:
            if bar['open'] >= stop: finish_position(bar, bar['open'], 'stop_gap'); return True
            if bar['high'] >= stop: finish_position(bar, stop, 'smart_stop' if pos['stop_state'].stage_index >= 0 else 'stop_loss'); return True
        return False

    def manage_open_position(bar: dict) -> None:
        nonlocal cash
        if position is None: return
        pos = position
        side = 1 if pos['direction'] == 'BUY' else -1
        risk = pos['initial_risk']
        for stage_index, (target_r, fraction) in enumerate(((1.0, 0.25), (2.0, 0.25))):
            if stage_index in pos['completed_targets']: continue
            target = pos['entry_price'] + side * target_r * risk
            touched = bar['high'] >= target if side == 1 else bar['low'] <= target
            if not touched: continue
            qty = min(pos['original_quantity'] * fraction, pos['remaining_quantity'])
            if qty <= 0: pos['completed_targets'].add(stage_index); continue
            fill = target * (1 - slippage_rate if side == 1 else 1 + slippage_rate)
            gross = (fill - pos['entry_price']) * qty * side
            fee = fill * qty * fee_rate
            cash += gross - fee
            pos['realized_gross'] += gross
            pos['exit_fees'] += fee
            pos['remaining_quantity'] -= qty
            pos['completed_targets'].add(stage_index)
        favorable = bar['high'] if side == 1 else bar['low']
        update = update_smart_stop(pos['stop_state'], favorable)
        pos['stop_state'] = update.state
        new_stop = update.state.current_stop
        crossed = bar['low'] <= new_stop if side == 1 else bar['high'] >= new_stop
        if crossed: finish_position(bar, new_stop, 'smart_stop')
    # i is the 5m candle on which a signal was confirmed; execution is at i+1 open.
    for i in range(1, len(five)):
        bar = five[i]
        check_exit(bar)

        signal_bar = five[i - 1]
        signal_close_time = signal_bar["timestamp"] + 300
        closed_hourly = [c for c in hourly if c["timestamp"] + 3600 <= signal_close_time]
        closed_fifteen = [c for c in fifteen if c["timestamp"] + 900 <= signal_close_time]
        closed_five = five[:i]

        gate_status = "insufficient_data"
        decision = None
        if len(closed_hourly) >= 10 and len(closed_fifteen) >= max(20, atr_period) and len(closed_five) >= 10:
            try:
                h_trend = classify_price_action_trend(closed_hourly[-trend_lookback:])
                m_trend = classify_price_action_trend(closed_fifteen[-trend_lookback:])
                f_trend = classify_price_action_trend(closed_five[-trend_lookback:])
                decision = evaluate_timeframe_gate(h_trend, m_trend, f_trend,
                                                   closed_fifteen[-1]["close"])
                gate_status = decision.status
            except (ValueError, TypeError, OverflowError):
                gate_status = "invalid_data"
        gate_counts[gate_status] = gate_counts.get(gate_status, 0) + 1

        if position is None and decision is not None and decision.allowed:
            try:
                atr_value = _atr(closed_fifteen, atr_period)
            except ValueError:
                atr_value = 0.0
            if atr_value > 0 and cash > 0:
                direction = "BUY" if decision.direction == "up" else "SELL"
                side = 1 if direction == "BUY" else -1
                raw_entry = bar["open"]
                entry = raw_entry * (1 + slippage_rate if side == 1 else 1 - slippage_rate)
                distance = atr_value * stop_atr_multiple
                risk_cash = cash * risk_fraction
                quantity = min(risk_cash / distance, cash / (entry * (1 + fee_rate)))
                if quantity > 0 and isfinite(quantity):
                    entry_fee = entry * quantity * fee_rate
                    cash -= entry_fee
                    stop = entry - distance if side == 1 else entry + distance
                    stop_state = create_smart_stop("long" if side == 1 else "short", entry, stop, atr_value)
                    position = {
                        "direction": direction, "entry_timestamp": bar["timestamp"],
                        "entry_price": entry, "original_quantity": quantity,
                        "remaining_quantity": quantity, "entry_fee": entry_fee,
                        "initial_risk": distance, "stop_state": stop_state,
                        "realized_gross": 0.0, "exit_fees": 0.0, "completed_targets": set(),
                    }
                    stop_hit = bar["low"] <= stop if side == 1 else bar["high"] >= stop
                    if stop_hit:
                        finish_position(bar, min(bar["open"], stop) if side == 1 else max(bar["open"], stop), "stop_loss_entry_bar")
                    else:
                        manage_open_position(bar)

        if position is None:
            mark = cash
        else:
            side = 1 if position["direction"] == "BUY" else -1
            unrealized = (bar["close"] - position["entry_price"]) * position["quantity"] * side
            estimated_exit_fee = bar["close"] * position["quantity"] * fee_rate
            mark = cash + unrealized - estimated_exit_fee
        curve.append(mark)
        peak = max(peak, mark)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - mark) / peak * 100)

    if position is not None:
        last = five[-1]
        close_position(last, last["close"], "end_of_data")
        curve.append(cash)
        peak = max(peak, cash)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - cash) / peak * 100)

    wins = sum(t.net_pnl > 0 for t in trades)
    gross_wins = sum(t.net_pnl for t in trades if t.net_pnl > 0)
    gross_losses = -sum(t.net_pnl for t in trades if t.net_pnl < 0)
    profit_factor = gross_wins / gross_losses if gross_losses else (None if not gross_wins else float("inf"))
    return ThreeTimeframeBacktestResult(
        starting_cash=starting_cash, ending_equity=cash, net_profit=cash - starting_cash,
        return_pct=(cash / starting_cash - 1) * 100, max_drawdown_pct=max_drawdown,
        win_rate_pct=(wins / len(trades) * 100) if trades else 0.0,
        profit_factor=profit_factor, trade_count=len(trades),
        buy_count=sum(t.direction == "BUY" for t in trades),
        sell_count=sum(t.direction == "SELL" for t in trades),
        trades=tuple(trades), equity_curve=tuple(curve),
        gate_counts=tuple(sorted(gate_counts.items())),
        warnings=(
            "این بک‌تست فقط پژوهشی است و سفارش واقعی یا اهرم ندارد.",
            "سیگنال فقط پس از بسته‌شدن کندل ۵ دقیقه و ورود در بازشدن کندل بعدی محاسبه می‌شود.",
            "کندل‌های ۱h و ۱۵m فقط پس از زمان بسته‌شدنشان قابل استفاده‌اند؛ نگاه به آینده مجاز نیست.",
            "کارمزد و لغزش قیمت اعمال شده‌اند؛ Funding، مالیات، عمق دفتر سفارش و اثر بازار لحاظ نشده‌اند.",
            "حد ضرر هوشمند: در +1R سربه‌سر، +2R قفل +1R، +3R قفل +2R و +4R قفل +3R؛ حد ضرر فقط تنگ‌تر می‌شود.",
            "حد سود پله‌ای: ۲۵٪ در +1R و ۲۵٪ در +2R؛ ۵۰٪ باقی‌مانده با حد ضرر هوشمند مدیریت می‌شود.",
            "دادهٔ محدود API عمومی نوبیتکس و نتیجهٔ تاریخی تضمین‌کنندهٔ عملکرد آینده نیست.",
        ),
    )
