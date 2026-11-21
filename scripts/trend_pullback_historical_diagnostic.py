#!/usr/bin/env python3
"""Walk-forward signal diagnostics for the research Trend Pullback engine."""
from __future__ import annotations
import csv
from bisect import bisect_right
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from lili_trader.trend_pullback_engine import TrendPullbackEngine

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/"data"/"history"
SYMBOLS=("BTCIRT","ETHIRT")
HORIZONS=(3,6,12)
SPLIT_FRACTION=0.70
MAX_HISTORY={"1h":500,"15m":500,"5m":2000}
INTERVAL={"1h":3600,"15m":900,"5m":300}

def load_rows(path):
    with path.open(newline="",encoding="utf-8") as f:
        rows=[{k:(int(r["timestamp"]) if k=="timestamp" else float(r[k]))
               for k in ("timestamp","open","high","low","close","volume")} for r in csv.DictReader(f)]
    if not rows: raise ValueError(f"empty history: {path}")
    prev=None
    for r in rows:
        if prev is not None and r["timestamp"]<=prev: raise ValueError("timestamps must be strictly increasing")
        prev=r["timestamp"]
        o,h,l,c,v=(r[k] for k in ("open","high","low","close","volume"))
        if not all(x>0 for x in (o,h,l,c)) or h<max(o,l,c) or l>min(o,h,c) or v<0:
            raise ValueError("invalid OHLCV row")
    return rows

def closed_prefix(rows,interval,as_of):
    return [r for r in rows if r["timestamp"]+interval<=as_of]

def freshness(rows,interval,as_of):
    if not rows: return False
    age=as_of-(rows[-1]["timestamp"]+interval)
    if age<0 or age>interval: return False
    if len(rows)>1 and rows[-1]["timestamp"]-rows[-2]["timestamp"]>2*interval: return False
    return True

def snapshot_at(hourly,fifteen,five,index):
    as_of=five[index]["timestamp"]+INTERVAL["5m"]
    h=closed_prefix(hourly,INTERVAL["1h"],as_of)[-MAX_HISTORY["1h"]:]
    m15=closed_prefix(fifteen,INTERVAL["15m"],as_of)[-MAX_HISTORY["15m"]:]
    m5=five[:index+1][-MAX_HISTORY["5m"]:]
    if not all((freshness(h,3600,as_of),freshness(m15,900,as_of),freshness(m5,300,as_of))):
        return None
    return {"as_of_timestamp":as_of,"candles":{
        "1h":[dict(r,is_closed=True) for r in h],
        "15m":[dict(r,is_closed=True) for r in m15],
        "5m":[dict(r,is_closed=True) for r in m5]}}

def atr14(rows,index,step=300):
    if index<14: return None
    window=rows[index-14:index+1]
    if len(window)!=15 or any(window[i]["timestamp"]-window[i-1]["timestamp"]!=step for i in range(1,len(window))):
        return None
    trs=[]
    for i in range(index-13,index+1):
        p=rows[i-1]["close"]
        trs.append(max(rows[i]["high"]-rows[i]["low"],abs(rows[i]["high"]-p),abs(rows[i]["low"]-p)))
    a=sum(trs)/len(trs)
    return a if a>0 else None

def signal_outcome(rows,index,direction,reference,horizon):
    if index+horizon>=len(rows): return None
    future=rows[index+1:index+horizon+1]
    if len(future)!=horizon or any(future[i]["timestamp"]-future[i-1]["timestamp"]!=300 for i in range(1,len(future))):
        return None
    a=atr14(rows,index)
    if a is None:return None
    d=1 if direction=="BUY" else -1
    ret=d*(future[-1]["close"]-reference)/a
    mfe=(max(r["high"] for r in future)-reference) if d==1 else (reference-min(r["low"] for r in future))
    mae=(reference-min(r["low"] for r in future)) if d==1 else (max(r["high"] for r in future)-reference)
    return {"return_atr":ret,"mfe_atr":max(0,mfe)/a,"mae_atr":max(0,mae)/a}

def metrics(samples):
    return {"n":len(samples),"median_return_atr":median(x["return_atr"] for x in samples) if samples else None,
            "median_mfe_atr":median(x["mfe_atr"] for x in samples) if samples else None,
            "median_mae_atr":median(x["mae_atr"] for x in samples) if samples else None,
            "return_positive_pct":100*sum(x["return_atr"]>0 for x in samples)/len(samples) if samples else None}

def fmt(v): return "n/a" if v is None else f"{v:.2f}"

def audit(symbol):
    h1=load_rows(DATA/f"{symbol}_60.csv")
    m15=load_rows(DATA/f"{symbol}_15.csv")
    m5=load_rows(DATA/f"{symbol}_5.csv")
    split=int(len(m5)*SPLIT_FRACTION)
    engine=TrendPullbackEngine()
    counts={"SIGNAL":0,"WAIT":0,"NO_SETUP":0,"INVALIDATED":0,"DISABLED":0,"stale_skip":0}
    signals=[]
    for i,row in enumerate(m5):
        snapshot=snapshot_at(h1,m15,m5,i)
        if snapshot is None:
            counts["stale_skip"]+=1; continue
        decision=engine.evaluate(snapshot)
        counts[decision.status]=counts.get(decision.status,0)+1
        if decision.status=="SIGNAL":
            evidence=decision.evidence
            signals.append({"index":i,"timestamp":row["timestamp"],"direction":evidence["direction"],
                            "reference_close":evidence["reference_close"],"as_of":evidence["as_of_timestamp"]})
    out=[f"{symbol} Trend Pullback historical diagnostic",
         f"Bars: 1h={len(h1)}, 15m={len(m15)}, 5m={len(m5)}; split at {datetime.fromtimestamp(m5[split]['timestamp'],timezone.utc).isoformat()}",
         f"Engine decision counts: {counts}",f"Research SIGNALs: {len(signals)} (no entry, SL, TP, position sizing or execution generated)"]
    for name,start,end in (("train",0,split),("holdout",split,len(m5))):
        group=[s for s in signals if start<=s["index"]<end]
        out.append(f"{name} signals={len(group)}")
        for h in HORIZONS:
            samples=[]
            for s in group:
                result=signal_outcome(m5,s["index"],s["direction"],s["reference_close"],h)
                if result is not None and (name!="train" or s["index"]+h<split):
                    samples.append(result)
            m=metrics(samples)
            out.append(f"  {h} bars: n={m['n']}; median return/MFE/MAE ATR={fmt(m['median_return_atr'])}/{fmt(m['median_mfe_atr'])}/{fmt(m['median_mae_atr'])}; positive close-return={fmt(m['return_positive_pct'])}%")
    return out

def main():
    report=["Trend Pullback engine historical diagnostic (research-only)","Signals are evaluated from closed, time-filtered H1/M15/M5 snapshots. No strategy PnL, orders, risk sizing, fees, slippage, or execution simulated."]
    for symbol in SYMBOLS: report.extend(["",*audit(symbol)])
    result="\n".join(report)
    print(result)
    (ROOT/"trend-pullback-historical-diagnostic.txt").write_text(result+"\n",encoding="utf-8")
if __name__=="__main__":main()
