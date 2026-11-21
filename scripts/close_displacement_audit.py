#!/usr/bin/env python3
"""Research-only close-displacement sensitivity test with chronological holdout."""
from __future__ import annotations
from pathlib import Path
from statistics import median
from lili_trader.market_structure import analyze_market_structure
try:
    from bos_choch_historical_diagnostic import FRAMES, SYMBOLS, load_rows, fmt
except ModuleNotFoundError:
    from scripts.bos_choch_historical_diagnostic import FRAMES, SYMBOLS, load_rows, fmt

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/"data"/"history"
THRESHOLDS=(0.0,0.1,0.25,0.5,1.0)
HORIZON=6
WINDOW=2

def event_key(e): return (e.kind,e.candle_index,round(float(e.level),8))

def atr14_contiguous(rows,index,step):
    if index<14: return None
    segment=rows[index-14:index+1]
    if len(segment)!=15 or any(segment[i]["timestamp"]-segment[i-1]["timestamp"]!=step for i in range(1,len(segment))):
        return None
    trs=[]
    for i in range(index-13,index+1):
        prev=rows[i-1]["close"]
        trs.append(max(rows[i]["high"]-rows[i]["low"],abs(rows[i]["high"]-prev),abs(rows[i]["low"]-prev)))
    value=sum(trs)/len(trs)
    return value if value>0 else None

def displacement_atr(rows,event,step):
    atr=atr14_contiguous(rows,event.candle_index,step)
    if atr is None: return None
    direction=1 if event.kind.endswith("_UP") else -1
    return direction*(event.close-event.level)/atr

def eligible_events(rows,events,step,threshold):
    return [e for e in events if (d:=displacement_atr(rows,e,step)) is not None and d>=threshold]

def segment_metrics(rows,events,step,horizon,start,end):
    samples=[]; skipped=0
    for e in events:
        i=e.candle_index
        if i<start or i+horizon>=end: continue
        atr=atr14_contiguous(rows,i,step)
        future=rows[i+1:i+horizon+1]
        if atr is None or len(future)!=horizon or any(future[j]["timestamp"]-future[j-1]["timestamp"]!=step for j in range(1,len(future))):
            skipped+=1; continue
        direction=1 if e.kind.endswith("_UP") else -1
        failed=any(r["close"]<=e.level if direction==1 else r["close"]>=e.level for r in future)
        favorable=max(r["high"] for r in future)-e.close if direction==1 else e.close-min(r["low"] for r in future)
        adverse=e.close-min(r["low"] for r in future) if direction==1 else max(r["high"] for r in future)-e.close
        samples.append((failed,max(0,favorable)/atr,max(0,adverse)/atr))
    return {"n":len(samples),"skipped":skipped,
            "reclaim_pct":100*sum(x[0] for x in samples)/len(samples) if samples else None,
            "median_fav_atr":median(x[1] for x in samples) if samples else None,
            "median_adv_atr":median(x[2] for x in samples) if samples else None,
            "fav_ge1_pct":100*sum(x[1]>=1 for x in samples)/len(samples) if samples else None}

def causality(rows,window=WINDOW):
    full=analyze_market_structure(rows,pivot_window=window)
    checks=[]
    for fraction in (.25,.5,.75):
        cut=int(len(rows)*fraction)
        if cut>=len(rows): continue
        partial=analyze_market_structure(rows[:cut],pivot_window=window)
        expected=[event_key(e) for e in full.events if e.candle_index<cut]
        actual=[event_key(e) for e in partial.events]
        checks.append((cut,expected==actual))
    return checks

def audit(symbol,frame,rows,step):
    events=analyze_market_structure(rows,pivot_window=WINDOW).events
    split=int(len(rows)*0.7)
    lines=[f"{symbol} {frame} displacement threshold (ATR14; horizon={HORIZON} candles; 70/30 chronological split):",
           "  threshold | eligible train | valid train | reclaim train | eligible test | valid test | reclaim test | median fav/adv ATR test | >=1ATR fav test"]
    for threshold in THRESHOLDS:
        filtered=eligible_events(rows,events,step,threshold)
        train=segment_metrics(rows,filtered,step,HORIZON,0,split)
        test=segment_metrics(rows,filtered,step,HORIZON,split,len(rows))
        lines.append(f"  {threshold:.2f} | {sum(e.candle_index<split for e in filtered)} | {train['n']} | {fmt(train['reclaim_pct'])}% | {sum(e.candle_index>=split for e in filtered)} | {test['n']} | {fmt(test['reclaim_pct'])}% | {fmt(test['median_fav_atr'])}/{fmt(test['median_adv_atr'])} | {fmt(test['fav_ge1_pct'])}%")
    checks=causality(rows)
    lines.append("  Prefix causality: "+(", ".join(f"{n}={'PASS' if ok else 'FAIL'}" for n,ok in checks) or "none"))
    return lines, bool(checks) and all(ok for _,ok in checks)

def main():
    report=["Li-Li Trader close-displacement filter research audit","Operational level-reclaim labels; not false-signal ground truth or strategy expectancy.","ATR and forward outcomes require contiguous candles; test segment is chronological last 30%. No trades, PnL, costs, or execution simulated."]
    failures=0
    for symbol in SYMBOLS:
        report.append(f"\n## {symbol}")
        for suffix,frame,step in FRAMES:
            path=DATA/f"{symbol}_{suffix}.csv"
            if not path.exists(): report.append(f"{frame}: MISSING"); failures+=1; continue
            lines,ok=audit(symbol,frame,load_rows(path),step); report.extend(lines)
            if not ok: failures+=1
    report.append(f"\nFinal: {'PASS' if failures==0 else 'FAIL'}; causal-check failures={failures}")
    result="\n".join(report); print(result)
    (ROOT/"close-displacement-audit.txt").write_text(result+"\n",encoding="utf-8")
    if failures: raise SystemExit(1)
if __name__=="__main__": main()
