#!/usr/bin/env python3
"""Ultra-minimal: just TRMB KDJ+Support 1yr scan"""
import sys, os, warnings; warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collections import defaultdict
import duckdb, pandas as pd
from indicator_calculator import calculate_all_indicators
from indicator_signals import detect_kdj_signals
from pattern_detector import detect_support_resistance

FWD=5; THR=0.02

def load(sym, start=None):
    conn = duckdb.connect('data/prices.ddb', read_only=True)
    if start:
        df = pd.read_sql_query("SELECT trade_date as date,open,high,low,close,volume FROM stock_prices WHERE symbol=? AND trade_date>=? ORDER BY trade_date", conn, params=(sym,start))
    else:
        df = pd.read_sql_query("SELECT trade_date as date,open,high,low,close,volume FROM stock_prices WHERE symbol=? ORDER BY trade_date", conn, params=(sym,))
    conn.close()
    df['date'] = pd.to_datetime(df['date'])
    return df

def run(name, start=None):
    print(f"[{name}] Loading...", end=" ", flush=True)
    df = load('TRMB', start)
    print(f"{len(df)} rows")
    if len(df) < 30: return {}
    df = df.reset_index(drop=True)
    df = calculate_all_indicators(df)
    # Only build support pattern index (fast)
    supports = {}
    pats = detect_support_resistance(df)
    for p in pats:
        if p.direction == 'bullish' and p.name == 'Support':
            for idx in p.indices:
                supports[idx] = 'Support'
    # Scan
    agg = defaultdict(lambda:{'count':0,'successes':0,'total_return':0.0})
    for i in range(20, len(df)):
        kdj_sigs = detect_kdj_signals(df, i)
        kdj_bull = [s for s in kdj_sigs if s.signal_type=='bullish' and s.name=='KDJ 超賣區金叉']
        has_pat = i in supports
        for sig in kdj_bull:
            if i+FWD >= len(df): continue
            entry = df['close'].iloc[i]; exitp = df['close'].iloc[i+FWD]
            ret = (exitp-entry)/entry
            is_suc = ret > THR
            key = (sig.name, supports.get(i,'None'), has_pat, False)
            agg[key]['count'] += 1; agg[key]['total_return'] += ret
            if is_suc: agg[key]['successes'] += 1
    result = {}
    for k,v in agg.items():
        if v['count'] < 3: continue
        result[k] = f"win={v['successes']/v['count']:.1%} avg={v['total_return']/v['count']:+.2f}% n={v['count']}"
    return result

if __name__ == '__main__':
    r1 = run("1年 (2025-05-26起)", '2025-05-26')
    rF = run("全量 (2021-10-07起)")
    print("\n=== TRMB | KDJ 超賣區金叉 | Support ===")
    for k,v in r1.items(): print(f"  [1年] {k}: {v}")
    for k,v in rF.items(): print(f"  [全量] {k}: {v}")