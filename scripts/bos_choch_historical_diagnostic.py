#!/usr/bin/env python3
"""Exploratory, read-only BOS/CHoCH historical outcome audit. Not a strategy backtest."""
from __future__ import annotations
import csv
from datetime import datetime, timezone
from statistics import median
from pathlib import Path
from lili_trader.market_structure import analyze_market_structure

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "history"
SYMBOLS = ("BTCIRT", "ETHIRT")
FRAMES = (("5", "5m", 300), ("15", "15m", 900), ("60", "1h", 3600),
          ("240", "4h", 14400), ("D", "1d", 86400))
HORIZONS = (3, 6, 12)
UP = {"BOS_UP", "CHOCH_UP", "BREAK_UP"}
DOWN = {"BOS_DOWN", "CHOCH_DOWN", "BREAK_DOWN"}

def load_rows(path):
    with path.open(newline="", encoding="utf-8") as f:
        rows = [{k: (int(r["timestamp"]) if k == "timestamp" else float(r[k]))
                 for k in ("timestamp", "open", "high", "low", "close", "volume")}
                for r in csv.DictReader(f)]
    if not rows:
        raise ValueError(f"empty CSV: {path}")
    return rows

def quality(rows, expected_step):
    ts = [r["timestamp"] for r in rows]
    duplicates = len(ts) - len(set(ts))
    nonmonotonic = sum(b <= a for a, b in zip(ts, ts[1:]))
    gaps = sum(max(0, (b-a)//expected_step-1) for a,b in zip(ts,ts[1:]) if b>a)
    malformed = 0
    for r in rows:
        o,h,l,c = (r[k] for k in ("open","high","low","close"))
        if not all(v > 0 for v in (o,h,l,c)) or h < max(o,l,c) or l > min(o,h,c):
            malformed += 1
    return duplicates, nonmonotonic, gaps, malformed

def atr_at(rows, index, period=14):
    start=max(1,index-period+1)
    trs=[]
    for i in range(start,index+1):
        prev=rows[i-1]["close"]
        trs.append(max(rows[i]["high"]-rows[i]["low"],
                      abs(rows[i]["high"]-prev),abs(rows[i]["low"]-prev)))
    return sum(trs)/len(trs) if trs else None

def event_signature(e):
    return (e.kind,e.level,e.candle_index,e.broken_swing_index,e.close)

def prefix_causality(rows, window=2):
    full=analyze_market_structure(rows,pivot_window=window)
    cuts=sorted(set(max(2*window+3, int(len(rows)*fraction))
                    for fraction in (0.25,0.5,0.75) if int(len(rows)*fraction)<len(rows)))
    results=[]
    for cut in cuts:
        partial=analyze_market_structure(rows[:cut],pivot_window=window)
        expected=[event_signature(e) for e in full.events if e.candle_index<cut]
        actual=[event_signature(e) for e in partial.events]
        results.append((cut, expected==actual))
    return results

def outcome_stats(rows, events, horizon):
    completed=[e for e in events if e.candle_index+horizon < len(rows)]
    if not completed:
        return {"n":0,"fail_pct":None,"median_fav_atr":None,"median_adv_atr":None,
                "fav_ge_1atr_pct":None}
    failed=0; fav=[]; adv=[]
    for e in completed:
        direction=1 if e.kind in UP else -1
        future=rows[e.candle_index+1:e.candle_index+horizon+1]
        failed += any((r["close"] <= e.level if direction==1 else r["close"] >= e.level) for r in future)
        a=atr_at(rows,e.candle_index)
        if not a or a<=0: continue
        favorable=(max(r["high"] for r in future)-e.close if direction==1
                   else e.close-min(r["low"] for r in future))
        adverse=(e.close-min(r["low"] for r in future) if direction==1
                 else max(r["high"] for r in future)-e.close)
        fav.append(max(0.0,favorable)/a); adv.append(max(0.0,adverse)/a)
    return {"n":len(completed),"fail_pct":100*failed/len(completed),
            "median_fav_atr":median(fav) if fav else None,
            "median_adv_atr":median(adv) if adv else None,
            "fav_ge_1atr_pct":100*sum(v>=1 for v in fav)/len(fav) if fav else None}

def fmt(value, digits=2):
    return "n/a" if value is None else f"{value:.{digits}f}"

def audit_dataset(symbol, label, step, rows):
    core=analyze_market_structure(rows,pivot_window=2)
    events=list(core.events)
    counts={k:sum(e.kind==k for e in events) for k in sorted(UP|DOWN)}
    q=quality(rows,step)
    out=[f"{symbol} {label}: bars={len(rows)}; UTC {datetime.fromtimestamp(rows[0]['timestamp'],timezone.utc).isoformat()} -> {datetime.fromtimestamp(rows[-1]['timestamp'],timezone.utc).isoformat()}",
         f"Data quality: duplicates={q[0]}, nonmonotonic={q[1]}, estimated missing intervals={q[2]}, invalid OHLC={q[3]}",
         f"Engine: bias={core.bias}; events={len(events)}; counts={counts}"]
    for horizon in HORIZONS:
        stats=outcome_stats(rows,events,horizon)
        out.append(f"Forward {horizon} bars (exploratory, complete samples only): n={stats['n']}; level-reclaim failure={fmt(stats['fail_pct'])}%; median favorable/adverse ATR={fmt(stats['median_fav_atr'])}/{fmt(stats['median_adv_atr'])}; favorable >=1 ATR={fmt(stats['fav_ge_1atr_pct'])}%")
    checks=prefix_causality(rows)
    out.append("Prefix causality: "+(", ".join(f"{cut} bars={'PASS' if ok else 'FAIL'}" for cut,ok in checks) or "not enough history"))
    return out, checks

def main():
    lines=["Li-Li Trader BOS/CHoCH historical diagnostic (exploratory only)",
           "Outcome labels are transparent definitions, not ground-truth false signals or a trading strategy.",
           "No entry/exit, PnL, broker, execution or live trading is simulated."]
    failures=0; warnings=0
    for symbol in SYMBOLS:
        lines.append(f"\n## {symbol}")
        for suffix,label,step in FRAMES:
            path=DATA/f"{symbol}_{suffix}.csv"
            if not path.exists():
                lines.append(f"{label}: MISSING"); failures+=1; continue
            rows=load_rows(path)
            q=quality(rows,step)
            block,checks=audit_dataset(symbol,label,step,rows)
            lines.extend(block)
            if q[0] or q[1] or q[3]: failures+=1
            if q[2]: warnings+=1
            if len(rows)<2*2+3: failures+=1
            if not checks or any(not ok for _,ok in checks): failures+=1
    lines.append(f"\nFinal: {'PASS' if failures==0 else 'FAIL'}; hard failures={failures}; gap warnings={warnings}")
    report="\n".join(lines)
    print(report)
    (ROOT/"bos-choch-historical-diagnostic.txt").write_text(report+"\n",encoding="utf-8")
    if failures: raise SystemExit(1)

if __name__=="__main__":
    main()
