"""Render a compact, auditable GitHub Actions summary for forward Paper runs."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def render_paper_summary(
    state_dir: str | Path,
    *,
    trigger_event: str = "unknown",
    cycle_outcome: str = "unknown",
    cycle_status: str = "unknown",
) -> str:
    directory = Path(state_dir)
    account_path = directory / "account.json"
    journal_path = directory / "decisions.jsonl"
    lines = [
        "# Li-Li Forward Paper",
        "",
        f"- Trigger: `{trigger_event}`",
        f"- Cycle step: `{cycle_outcome}`",
        f"- Cycle status: `{cycle_status}`",
    ]

    if account_path.exists():
        account = json.loads(account_path.read_text(encoding="utf-8"))
        lines.extend([
            f"- Mode: `{account.get('mode', 'unknown')}`",
            f"- Last cycle (UTC): `{account.get('last_cycle_at', 'unknown')}`",
            f"- USD/IRT seed: `{account.get('usd_irt_rate', 'unknown')}`",
            "",
            "| Symbol | Last market data (UTC) | New decision records | Closed trades | Open position | Realized equity (IRT) | Marked equity (IRT) |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ])
        for symbol in ("BTCIRT", "ETHIRT"):
            item = account.get("symbols", {}).get(symbol, {})
            lines.append(
                "| {symbol} | {market} | {decisions} | {closed} | {opened} | {realized} | {marked} |".format(
                    symbol=symbol,
                    market=item.get("last_market_data_at", "unknown"),
                    decisions=item.get("new_decisions_last_cycle", 0),
                    closed=item.get("closed_trades_last_cycle", 0),
                    opened="yes" if item.get("open_trade_id") else "no",
                    realized=item.get("last_equity_irt", "unknown"),
                    marked=item.get("last_marked_equity_irt", "unknown"),
                )
            )
    else:
        lines.extend([
            "",
            "Account state is not present. Check the cycle logs; initialization may still be required or the cycle may have failed.",
        ])

    if journal_path.exists():
        decisions = 0
        timely_decisions = 0
        late_skips = 0
        late_by_symbol: dict[str, int] = {}
        outcomes = 0
        reviews = 0
        wins = 0
        losses = 0
        net_pnl = 0.0
        actions: dict[str, int] = {}
        for raw in journal_path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            event: dict[str, Any] = json.loads(raw)
            payload = event.get("payload", {})
            event_type = event.get("event_type")
            if event_type == "DECISION":
                decisions += 1
                evidence = payload.get("evidence", {})
                skipped_late = (
                    isinstance(evidence, dict)
                    and evidence.get("skipped_late_signal") is True
                )
                if skipped_late:
                    late_skips += 1
                    symbol = payload.get("symbol", "unknown")
                    late_by_symbol[symbol] = late_by_symbol.get(symbol, 0) + 1
                else:
                    timely_decisions += 1
                    action = payload.get("action", "unknown")
                    actions[action] = actions.get(action, 0) + 1
            elif event_type == "OUTCOME":
                outcomes += 1
                pnl = float(payload.get("net_pnl_quote", 0.0))
                net_pnl += pnl
                wins += pnl > 0
                losses += pnl < 0
            elif event_type == "REVIEW":
                reviews += 1
        late_symbol_text = ", ".join(
            f"{symbol}: {late_by_symbol.get(symbol, 0)}"
            for symbol in ("BTCIRT", "ETHIRT")
        )
        lines.extend([
            "",
            f"- Journal decision records (including late skips): **{decisions}**",
            f"- Timely decisions evaluated: **{timely_decisions}**",
            f"- Late signals skipped (not entries): **{late_skips}** ({late_symbol_text})",
            f"- Journal outcomes: **{outcomes}** (wins: {wins}, losses: {losses})",
            f"- Net realized PnL (IRT): **{net_pnl:,.2f}**",
            f"- Human reviews: **{reviews}**",
            f"- Timely decision actions: `{json.dumps(actions, ensure_ascii=False, sort_keys=True)}`",
            "",
            "Late signals are reported separately and are never counted as timely entry decisions.",
        ])
    else:
        lines.extend(["", "- Journal: no decision file has been created yet."])

    return "\n".join(lines) + "\n"


def main() -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        raise SystemExit("GITHUB_STEP_SUMMARY is required")
    state_dir = os.environ.get("PAPER_STATE_DIR", "data/paper/forward")
    trigger_event = os.environ.get("TRIGGER_EVENT", "unknown")
    cycle_outcome = os.environ.get("CYCLE_OUTCOME", "unknown")
    cycle_status = os.environ.get("CYCLE_STATUS", "unknown")
    Path(summary_path).write_text(
        render_paper_summary(
            state_dir,
            trigger_event=trigger_event,
            cycle_outcome=cycle_outcome,
            cycle_status=cycle_status,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
