#!/usr/bin/env python3
"""Debug: find KDJ+Support coincidences in TRMB"""
import sys, os, warnings; warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import duckdb, pandas as pd
from indicator_calculator import calculate_all_indicators
from indicator_signals import detect_kdj_signals
from pattern_detector import detect_support_resistance

FWD=5

def load(sym, start=None):
    conn = duckdb.connect('data/prices.ddb', read_only=True)
    if start:
        df = pd.read_sql_query("SELECT trade_date as date,open,high,low,close,volume FROM stock_prices WHERE symbol=? AND trade_date>=? ORDER BY trade_date", conn, params=(sym,start))
    else:
        df = pd.read_sql_query("SELECT trade_date as date,open,high,low,close,volume FROM stock_prices WHERE symbol=? ORDER BY trade_date", conn, params=(sym,))
    conn.close()
    df['date'] = pd.to_datetime(df['date'])
    return df

for period, start in [("1年", '2025-05-26'), ("全量", None)]:
    df = load('TRMB', start)
    print(f"\n[{period}] {len(df)} rows")
    if len(df) < 30: continue
    df = df.reset_index(drop=True)
    df = calculate_all_indicators(df)

    # Build support index
    supports = {}
    pats = detect_support_resistance(df)
    print(f"  Support patterns: {len(pats)}")
    for p in pats:
        if p.direction == 'bullish' and p.name == 'Support':
            for idx in p.indices:
                supports[idx] = p.confidence

    print(f"  Unique support indices: {sorted(supports.keys())[:10]}...")

    kdj_dates = []
    for i in range(20, len(df)):
        sigs = detect_kdj_signals(df, i)
        kdj = [s for s in sigs if s.signal_type=='bullish' and s.name=='KDJ 超賣區金叉']
        if kdj:
            has_pat = i in supports
            kdj_dates.append((i, df['date'].iloc[i].date(), has_pat, supports.get(i,'-')))

    print(f"  KDJ 超賣區金叉 signals: {len(kdj_dates)}")
    for i, d, hp, conf in kdj_dates:
        print(f"    i={i} date={d} has_Support={hp} support_conf={conf}")