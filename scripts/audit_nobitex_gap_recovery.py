#!/usr/bin/env python3
"""Read-only live audit of Nobitex missing 5m candle recovery.

The repository history is copied to a temporary directory. The recovery code
may modify only those temporary copies; no repository data is rewritten.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lili_trader.history_gap_recovery import recover_missing_5m_candles
from lili_trader.nobitex_data import NobitexAdapter

SYMBOLS = ("BTCIRT", "ETHIRT")


def main() -> int:
    source_dir = ROOT / "data" / "history"
    report_dir = ROOT / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "nobitex-gap-recovery-audit.txt"
    lines = [
        "Read-only live Nobitex gap-recovery audit",
        "Repository data files are not modified; all recovery writes use temporary copies.",
        "",
    ]
    adapter = NobitexAdapter()
    with tempfile.TemporaryDirectory(prefix="lili-nobitex-gap-audit-") as temp:
        temp_dir = Path(temp)
        for symbol in SYMBOLS:
            source = source_dir / f"{symbol}_5.csv"
            if not source.is_file():
                lines.append(f"{symbol}: SKIPPED — stored 5m history file missing")
                continue
            target = temp_dir / source.name
            shutil.copy2(source, target)
            result = recover_missing_5m_candles(target, symbol, adapter)
            lines.extend([
                f"{symbol}: status={result['status']}",
                f"  gaps_before={result['gap_intervals_before']}",
                f"  missing_before={result['missing_candles_before']}",
                f"  missing_timestamps_before={result.get('missing_timestamps_before', [])}",
                f"  requests={result['attempted_queries']}",
                f"  recovered_5m={len(result['recovered_timestamps'])}",
                f"  recovered_from_1m={len(result['recovered_from_1m'])}",
                f"  remaining={result['missing_candles_after']}",
                f"  missing_timestamps_after={result.get('missing_timestamps_after', [])}",
                f"  recovered_timestamps={result['recovered_timestamps']}",
                f"  errors={result['errors']}",
            ])
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"Audit report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
