#!/usr/bin/env python3
"""Sensitivity audit of BOS/CHoCH pivot confirmation window; descriptive only."""
from __future__ import annotations
from pathlib import Path
try:
    from bos_choch_historical_diagnostic import FRAMES, SYMBOLS, load_rows, outcome_stats, fmt
except ModuleNotFoundError:
    from scripts.bos_choch_historical_diagnostic import FRAMES, SYMBOLS, load_rows, outcome_stats, fmt
from lili_trader.market_structure import analyze_market_structure
ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/"data"/"history"
WINDOWS=(1,2,3,4,5)
HORIZON=6

def event_key(e): return (e.kind,e.candle_index,round(float(e.level),8))

def causality_checks(rows,window):
    full=analyze_market_structure(rows,pivot_window=window)
    cuts=sorted({max(2*window+3,int(len(rows)*f)) for f in (.25,.5,.75) if int(len(rows)*f)<len(rows)})
    checks=[]
    for cut in cuts:
        prefix=analyze_market_structure(rows[:cut],pivot_window=window)
        prior=[e for e in full.events if e.candle_index<cut]
        checks.append((cut,[event_key(e) for e in prior]==[event_key(e) for e in prefix.events]))
    return checks

def report_dataset(symbol,frame,rows):
    lines=[f"{symbol} {frame} pivot-window sensitivity (6-bar horizon):"]
    analyses={}; metrics={}; ok_all=True
    for w in WINDOWS:
        a=analyze_market_structure(rows,pivot_window=w); events=list(a.events)
        metrics[w]=outcome_stats(rows,events,HORIZON); analyses[w]=a
        checks=causality_checks(rows,w)
        if not checks or any(not ok for _,ok in checks): ok_all=False
    baseline={event_key(e) for e in analyses[2].events}
    for w in WINDOWS:
        keys={event_key(e) for e in analyses[w].events}; shared=len(keys&baseline); n=len(baseline); s=metrics[w]
        lines.append(f"  window={w}: events={len(keys)}; shared w2={shared}/{n} ({fmt(100*shared/n if n else None)}%); reclaim-failure={fmt(s['fail_pct'])}%; median favorable/adverse ATR={fmt(s['median_fav_atr'])}/{fmt(s['median_adv_atr'])}; >=1 ATR favorable={fmt(s['fav_ge_1atr_pct'])}%; causality={'PASS' if all(ok for _,ok in causality_checks(rows,w)) else 'FAIL'}")
    return lines,ok_all

def main():
    report=["Li-Li Trader pivot-window sensitivity audit","Descriptive robustness study; not parameter optimization or strategy backtest.","Events compared with window=2 using type, candle index and broken level; outcome horizon=6 candles."]
    failures=0
    for symbol in SYMBOLS:
        report.append(f"\n## {symbol}")
        for suffix,frame,step in FRAMES:
            p=DATA/f"{symbol}_{suffix}.csv"
            if not p.exists(): report.append(f"{frame}: MISSING"); failures+=1; continue
            lines,ok=report_dataset(symbol,frame,load_rows(p)); report.extend(lines)
            if not ok: failures+=1
    report.append(f"\nFinal: {'PASS' if failures==0 else 'FAIL'}; causal-check failures={failures}")
    result="\n".join(report); print(result)
    (ROOT/"pivot-window-sensitivity.txt").write_text(result+"\n",encoding="utf-8")
    if failures: raise SystemExit(1)
if __name__=="__main__": main()
