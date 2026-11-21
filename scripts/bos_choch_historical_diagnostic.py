#!/usr/bin/env python3
"""Exploratory, read-only BOS/CHoCH historical outcome audit. Not a strategy backtest."""
from __future__ import annotations
import csv
from datetime import datetime, timezone
from statistics import median
from pathlib import Path
from lili_trader.market_structure import analyze_market_structure

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/"data"/"history"
SYMBOLS=("BTCIRT","ETHIRT")
FRAMES=(("5","5m",300),("15","15m",900),("60","1h",3600),("240","4h",14400),("D","1d",86400))
HORIZONS=(3,6,12)
UP={"BOS_UP","CHOCH_UP","BREAK_UP"}
DOWN={"BOS_DOWN","CHOCH_DOWN","BREAK_DOWN"}
GROUPS={"BOS":{"BOS_UP","BOS_DOWN"},"CHoCH":{"CHOCH_UP","CHOCH_DOWN"},"Initial break":{"BREAK_UP","BREAK_DOWN"}}

def load_rows(path):
    with path.open(newline="",encoding="utf-8") as f:
        rows=[{k:(int(r["timestamp"]) if k=="timestamp" else float(r[k])) for k in ("timestamp","open","high","low","close","volume")} for r in csv.DictReader(f)]
    if not rows: raise ValueError(f"empty CSV: {path}")
    return rows

def quality(rows,step):
    ts=[r["timestamp"] for r in rows]
    dup=len(ts)-len(set(ts))
    nonmono=sum(b<=a for a,b in zip(ts,ts[1:]))
    gaps=sum(max(0,(b-a)//step-1) for a,b in zip(ts,ts[1:]) if b>a)
    bad=sum(not all(v>0 for v in (r["open"],r["high"],r["low"],r["close"])) or r["high"]<max(r["open"],r["low"],r["close"]) or r["low"]>min(r["open"],r["high"],r["close"]) for r in rows)
    return dup,nonmono,gaps,bad

def closed_prefix(rows,interval,cutoff):
    """Only use higher-timeframe candles whose full interval has closed."""
    return [r for r in rows if r["timestamp"]+interval<=cutoff]

def atr_at(rows,index,period=14):
    start=max(1,index-period+1)
    trs=[]
    for i in range(start,index+1):
        prev=rows[i-1]["close"]
        trs.append(max(rows[i]["high"]-rows[i]["low"],abs(rows[i]["high"]-prev),abs(rows[i]["low"]-prev)))
    return sum(trs)/len(trs) if trs else None

def signature(e):
    return (e.kind,e.level,e.candle_index,e.broken_swing_index,e.close)

def prefix_causality(rows,window=2):
    full=analyze_market_structure(rows,pivot_window=window)
    cuts=[]
    for fraction in (.25,.5,.75):
        cut=int(len(rows)*fraction)
        if cut<len(rows): cuts.append(max(2*window+3,cut))
    cuts=sorted(set(cuts))
    checks=[]
    for cut in cuts:
        partial=analyze_market_structure(rows[:cut],pivot_window=window)
        checks.append((cut,[signature(e) for e in full.events if e.candle_index<cut]==[signature(e) for e in partial.events]))
    return checks

def outcome_stats(rows,events,horizon):
    done=[e for e in events if e.candle_index+horizon<len(rows)]
    if not done: return {"n":0,"fail_pct":None,"median_fav_atr":None,"median_adv_atr":None,"fav_ge_1atr_pct":None}
    failed=0; fav=[]; adv=[]
    for e in done:
        direction=1 if e.kind in UP else -1
        future=rows[e.candle_index+1:e.candle_index+horizon+1]
        failed+=any(r["close"]<=e.level if direction==1 else r["close"]>=e.level for r in future)
        a=atr_at(rows,e.candle_index)
        if not a or a<=0: continue
        favorable=max(r["high"] for r in future)-e.close if direction==1 else e.close-min(r["low"] for r in future)
        adverse=e.close-min(r["low"] for r in future) if direction==1 else max(r["high"] for r in future)-e.close
        fav.append(max(0,favorable)/a); adv.append(max(0,adverse)/a)
    return {"n":len(done),"fail_pct":100*failed/len(done),"median_fav_atr":median(fav) if fav else None,
            "median_adv_atr":median(adv) if adv else None,"fav_ge_1atr_pct":100*sum(x>=1 for x in fav)/len(fav) if fav else None}

def fmt(v,d=2): return "n/a" if v is None else f"{v:.{d}f}"

def mtf_buckets(lower_rows,lower_step,events,h1_rows,m15_rows=None,window=2):
    """Group events by direction of only fully closed higher-timeframe candles."""
    buckets={"aligned_1h":[],"opposed_1h":[],"neutral_1h":[]}
    if m15_rows is not None: buckets["aligned_1h_and_15m"]=[]
    cache={}
    for e in events:
        if e.timestamp is None or e.candle_index>=len(lower_rows): continue
        close_time=lower_rows[e.candle_index]["timestamp"]+lower_step
        direction="up" if e.kind in UP else "down"
        key=("1h",close_time)
        if key not in cache:
            p=closed_prefix(h1_rows,3600,close_time)
            cache[key]=analyze_market_structure(p,pivot_window=window).bias if p else "unknown"
        bias1=cache[key]
        if bias1==direction: buckets["aligned_1h"].append(e)
        elif bias1 in ("up","down"): buckets["opposed_1h"].append(e)
        else: buckets["neutral_1h"].append(e)
        if m15_rows is not None:
            key=("15m",close_time)
            if key not in cache:
                p=closed_prefix(m15_rows,900,close_time)
                cache[key]=analyze_market_structure(p,pivot_window=window).bias if p else "unknown"
            if bias1==direction and cache[key]==direction: buckets["aligned_1h_and_15m"].append(e)
    return buckets

def audit_dataset(symbol,label,step,rows):
    core=analyze_market_structure(rows,pivot_window=2); events=list(core.events); q=quality(rows,step)
    counts={k:sum(e.kind==k for e in events) for k in sorted(UP|DOWN)}
    out=[f"{symbol} {label}: bars={len(rows)}; UTC {datetime.fromtimestamp(rows[0]['timestamp'],timezone.utc).isoformat()} -> {datetime.fromtimestamp(rows[-1]['timestamp'],timezone.utc).isoformat()}",
         f"Data quality: duplicates={q[0]}, nonmonotonic={q[1]}, estimated missing intervals={q[2]}, invalid OHLC={q[3]}",
         f"Engine: bias={core.bias}; events={len(events)}; counts={counts}"]
    for h in HORIZONS:
        s=outcome_stats(rows,events,h)
        out.append(f"Forward {h} bars: n={s['n']}; level-reclaim failure={fmt(s['fail_pct'])}%; median favorable/adverse ATR={fmt(s['median_fav_atr'])}/{fmt(s['median_adv_atr'])}; favorable >=1 ATR={fmt(s['fav_ge_1atr_pct'])}%")
    out.append("Event-class comparison at 6 bars (small samples; descriptive only):")
    for name,kinds in GROUPS.items():
        s=outcome_stats(rows,[e for e in events if e.kind in kinds],6)
        out.append(f"  {name}: n={s['n']}; reclaim-failure={fmt(s['fail_pct'])}%; median favorable/adverse ATR={fmt(s['median_fav_atr'])}/{fmt(s['median_adv_atr'])}; >=1 ATR favorable={fmt(s['fav_ge_1atr_pct'])}%")
    checks=prefix_causality(rows)
    out.append("Prefix causality: "+(", ".join(f"{n} bars={'PASS' if ok else 'FAIL'}" for n,ok in checks) or "not enough history")
    return out,checks

def audit_mtf(symbol,name,rows,step,events,h1,m15=None):
    buckets=mtf_buckets(rows,step,events,h1,m15)
    out=[f"\nMTF-conditioned {symbol} {name} (higher-TF candles fully closed; 6-bar outcome):"]
    for label,subset in buckets.items():
        s=outcome_stats(rows,subset,6)
        out.append(f"  {label}: n={s['n']}; reclaim-failure={fmt(s['fail_pct'])}%; median favorable/adverse ATR={fmt(s['median_fav_atr'])}/{fmt(s['median_adv_atr'])}; >=1 ATR favorable={fmt(s['fav_ge_1atr_pct'])}%")
    return out

def main():
    report=["Li-Li Trader BOS/CHoCH historical diagnostic (exploratory only)","Operational reclaim labels are not ground-truth false signals.","No strategy, entries, exits, PnL, broker, execution or live trading simulated."]
    failures=warnings=0
    for symbol in SYMBOLS:
        report.append(f"\n## {symbol}"); loaded={}
        for suffix,label,step in FRAMES:
            path=DATA/f"{symbol}_{suffix}.csv"
            if not path.exists(): report.append(f"{label}: MISSING"); failures+=1; continue
            rows=load_rows(path); loaded[label]=rows; q=quality(rows,step)
            block,checks=audit_dataset(symbol,label,step,rows); report.extend(block)
            if q[0] or q[1] or q[3] or len(rows)<7 or not checks or any(not ok for _,ok in checks): failures+=1
            if q[2]: warnings+=1
        if all(x in loaded for x in ("5m","15m","1h")):
            e5=analyze_market_structure(loaded["5m"],pivot_window=2).events
            e15=analyze_market_structure(loaded["15m"],pivot_window=2).events
            report.extend(audit_mtf(symbol,"5m",loaded["5m"],300,e5,loaded["1h"],loaded["15m"]))
            report.extend(audit_mtf(symbol,"15m",loaded["15m"],900,e15,loaded["1h"]))
    report.append(f"\nFinal: {'PASS' if failures==0 else 'FAIL'}; hard failures={failures}; gap warnings={warnings}")
    text_report="\n".join(report)
    print(text_report)
    (ROOT/"bos-choch-historical-diagnostic.txt").write_text(text_report+"\n",encoding="utf-8")
    if failures: raise SystemExit(1)

if __name__=="__main__": main()
