#!/usr/bin/env python3
"""Collect a bounded live sample from Nobitex public candle WebSocket channels."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from lili_trader.nobitex_websocket import NobitexCandleWebSocket

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration-seconds", type=int, default=120)
    parser.add_argument("--output", type=Path,
        default=ROOT / "data" / "market" / "live" / "nobitex-candle-stream.jsonl")
    args = parser.parse_args()
    result = NobitexCandleWebSocket().stream(
        duration_seconds=args.duration_seconds, output_path=args.output)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if result["total_publications"] > 0 else 2

if __name__ == "__main__":
    raise SystemExit(main())
