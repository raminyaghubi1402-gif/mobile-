#!/usr/bin/env python3
"""Read-only historical market-structure diagnostic; no strategy/PnL evaluation."""
import csv
from datetime import datetime, timezone
from pathlib import Path

from lili_trader.market_structure import analyze_market_structure
from lili_trader.market_structure_context import analyze_market_context, analyze_timeframe_alignment

ROOT=Path(__file__).resolve().parents[1]
HISTORY=ROOT/"data"/"history"
SYMBOLS=("BTCIRT","ETHIRT")
FRAMES=(("5","5m",300),("15","15m",900),("60","1h",3600),("240","4h",14400),("D","1d",86400))

def load_csv(path):
    with path.open(newline="",encoding="utf-8") as f:
        return [{k:(int(r["timestamp"]) if k=="timestamp" else float(r[k]))
                 for k in ("timestamp","open","high","low","close","volume")}
                for r in csv.DictReader(f)]

def dt(ts):
    return datetime.fromtimestamp(ts,tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

def signature(e):
    return (e.kind,e.level,e.candle_index,e.broken_swing_index,e.close)

def prefix_check(rows, sizes):
    full=analyze_market_structure(rows,pivot_window=2)
    full_context=analyze_market_context(rows,pivot_window=2)
    results=[]
    for size in sizes:
        if 8<=size<len(rows):
            prefix=rows[:size]
            p=analyze_market_structure(prefix,pivot_window=2)
            pc=analyze_market_context(prefix,pivot_window=2)
            core_ok=([signature(e) for e in full.events if e.candle_index<size]
                     ==[signature(e) for e in p.events])
            sweep_sig=lambda s:(s.kind,s.level,s.swing_index,s.candle_index,s.close)
            context_ok=([sweep_sig(s) for s in full_context.sweeps if s.candle_index<size]
                        ==[sweep_sig(s) for s in pc.sweeps])
            results.append((size,core_ok and context_ok))
    return results

def main():
    lines=["Li-Li market-structure historical audit (diagnostic only)",
           "No trades, signals, PnL, or execution simulated.",
           "Data: checked-in Nobitex history CSV snapshots."]
    failures=0; quality_warnings=0
    for symbol in SYMBOLS:
        frames={}
        lines.append(f"\n## {symbol}")
        for suffix,label,step in FRAMES:
            path=HISTORY/f"{symbol}_{suffix}.csv"
            if not path.exists():
                lines.append(f"{label}: MISSING"); failures+=1; continue
            rows=load_csv(path); frames[label]=rows
            if not rows:
                lines.append(f"{label}: EMPTY"); failures+=1; continue
            ts=[r["timestamp"] for r in rows]
            duplicates=len(ts)-len(set(ts))
            nonmonotonic=sum(b<=a for a,b in zip(ts,ts[1:]))
            gaps=sum(max(0,(b-a)//step-1) for a,b in zip(ts,ts[1:]) if b>a)
            core=analyze_market_structure(rows,pivot_window=2)
            recent=rows[-min(900,len(rows)):]
            ctx=analyze_market_context(recent,pivot_window=2)
            labels=[s.label for s in ctx.labels]
            label_counts={k:labels.count(k) for k in ("HH","HL","LH","LL","EQH","EQL")}
            event_counts={kind:sum(e.kind==kind for e in core.events)
                          for kind in ("BOS_UP","BOS_DOWN","CHOCH_UP","CHOCH_DOWN","BREAK_UP","BREAK_DOWN")}
            bos=event_counts["BOS_UP"]+event_counts["BOS_DOWN"]
            choch=event_counts["CHOCH_UP"]+event_counts["CHOCH_DOWN"]
            lines.append(f"{label}: {len(rows)} bars, {dt(ts[0])} to {dt(ts[-1])}; duplicate={duplicates}, nonmonotonic={nonmonotonic}, estimated missing bars={gaps}; bias={core.bias}; BOS={bos}, CHoCH={choch}; confirmed swings={len(core.swings)}; recent labels={label_counts}; sweeps={len(ctx.sweeps)}; range={ctx.range_context.status}")
            if duplicates or nonmonotonic:
                failures+=1
            if gaps:
                quality_warnings+=1
        if all(t in frames for t in ("1h","15m","5m")):
            cutoff=min(frames[t][-1]["timestamp"] for t in ("1h","15m","5m"))
            synced={t:[r for r in frames[t] if r["timestamp"]<=cutoff] for t in ("1h","15m","5m")}
            if all(len(x)>=20 for x in synced.values()):
                a=analyze_timeframe_alignment(synced,pivot_window=2)
                lines.append(f"MTF aligned cutoff {dt(cutoff)}: biases={a.biases}; consensus={a.consensus}; aligned={a.aligned}; conflicting={a.conflicting}; {a.reason_fa}")
            else:
                lines.append("MTF: skipped (not enough synchronized candles)")
        if "5m" in frames:
            recent=frames["5m"][-1000:]
            checks=prefix_check(recent,(100,250,500,750))
            lines.append("Prefix/look-ahead consistency: "+", ".join(f"{n} bars={'PASS' if ok else 'FAIL'}" for n,ok in checks))
            if not checks or any(not ok for _,ok in checks): failures+=1
    lines.append(f"\nAudit: {'PASS' if failures==0 else 'FAIL'}; hard failures={failures}; data-gap warnings={quality_warnings}")
    report="\n".join(lines)
    print(report)
    (ROOT/"market-structure-historical-audit.txt").write_text(report+"\n",encoding="utf-8")
    if failures: raise SystemExit(1)

if __name__=="__main__":
    main()
