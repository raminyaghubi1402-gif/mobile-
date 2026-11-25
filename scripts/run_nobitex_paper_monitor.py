#!/usr/bin/env python3
"""Run one public-data Nobitex paper-monitor observation."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lili_trader.live_paper_monitor import run_live_paper_monitor


def main() -> int:
    result = run_live_paper_monitor(
        journal_path=ROOT / "data" / "paper" / "live" / "decisions.jsonl",
        state_path=ROOT / "data" / "paper" / "live" / "state.json",
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    if all(item.get("status") != "ok" for item in result["symbols"].values()):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
