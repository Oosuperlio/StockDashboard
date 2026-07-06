#!/usr/bin/env python3
"""Force-regenerate ALL three BEST_COMBOS dictionaries in signal_scanner.py"""
import pandas as pd
import math
import re

STOCK_CSV = 'backtest_4way_results.csv'
SECTOR_CSV = 'backtest_sector_results.csv'
SCANNER = 'signal_scanner.py'
MIN_N = 5

# Load CSVs
stock_df = pd.read_csv(STOCK_CSV)
sector_df = pd.read_csv(SECTOR_CSV)

# Fill NaN in CSVs
for df in [stock_df, sector_df]:
    df['avg_return'] = df['avg_return'].fillna(0.0)
    df['win_rate'] = df['win_rate'].fillna(0.5)

def clean_pat(val):
    """Convert pattern to safe string, never None/NaN"""
    if isinstance(val, str):
        return val
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return 'None'
    return str(val)

def esc(s):
    return s.replace("'", "\\'") if isinstance(s, str) else s

def fmt_key(k):
    return repr(tuple(esc(x) for x in k))

def safe_val(wr, ar, cnt):
    if math.isnan(wr): wr = 0.5
    if math.isnan(ar): ar = 0.0
    return (round(wr, 4), round(ar, 4), int(cnt))

# ── STOCK ──
stock_entries = []
for _, r in stock_df.iterrows():
    if r['count'] < MIN_N: continue
    k = (r['symbol'], r['sector'], r['signal'],
         clean_pat(r['matched_pattern']),
         'Y' if r['volume_confirmed'] else 'N',
         'Y' if r['has_pattern'] else 'N')
    stock_entries.append((k, safe_val(r['win_rate'], r['avg_return'], r['count'])))

# ── SECTOR ──
sector_entries = []
for _, r in sector_df.iterrows():
    if r['count'] < MIN_N: continue
    k = (r['sector'], r['signal'],
         clean_pat(r['matched_pattern']),
         'Y' if r['volume_confirmed'] else 'N',
         'Y' if r['has_pattern'] else 'N')
    sector_entries.append((k, safe_val(r['win_rate'], r['avg_return'], r['count'])))

# ── FALLBACK ──
fb_agg = {}
for _, r in sector_df.iterrows():
    if r['count'] < MIN_N: continue
    pat = clean_pat(r['matched_pattern'])
    k = (r['signal'], pat,
         'Y' if r['volume_confirmed'] else 'N',
         'Y' if r['has_pattern'] else 'N')
    a = fb_agg.setdefault(k, {'c':0, 's':0.0, 'r':0.0})
    c = int(r['count'])
    a['c'] += c
    a['s'] += r['win_rate'] * c
    a['r'] += r['avg_return'] * c

fb_entries = [(k, safe_val(a['s']/a['c'], a['r']/a['c'], a['c']))
              for k, a in sorted(fb_agg.items(), key=lambda x: -x[1]['c'])
              if a['c'] >= MIN_N]

# ── Generate code ──
def gen_code(name, entries):
    lines = [f"{name} = {{"]
    for k, v in entries:
        lines.append(f"    {fmt_key(k)}: ({v[0]}, {v[1]}, {v[2]}),")
    lines.append("}")
    return '\n'.join(lines)

stock_code = gen_code("BEST_COMBOS_STOCK", stock_entries)
sector_code = gen_code("BEST_COMBOS_SECTOR", sector_entries)
fb_code = gen_code("BEST_COMBOS_FALLBACK", fb_entries)

print(f"STOCK={len(stock_entries)}  SECTOR={len(sector_entries)}  FALLBACK={len(fb_entries)}")
print(f"CB in STOCK: {stock_code.count('Consolidation Breakout')}")
print(f"CB in FALLBACK: {fb_code.count('Consolidation Breakout')}")
print(f"nan in codes: {stock_code.count('nan') + sector_code.count('nan') + fb_code.count('nan')}")

# ── Replace in file ──
with open(SCANNER) as f:
    content = f.read()

# Find the three dictionary boundaries
s1 = content.index('BEST_COMBOS_STOCK = {')
s2 = content.index('BEST_COMBOS_SECTOR = {')
s3 = content.index('BEST_COMBOS_FALLBACK = {')

# FALLBACK closing brace
depth = 0
in_dict = False
fb_end = s3
for i in range(s3, len(content)):
    c = content[i]
    if c == '{' and not in_dict:
        depth = 1; in_dict = True
    elif c == '{': depth += 1
    elif c == '}':
        depth -= 1
        if depth == 0 and in_dict:
            fb_end = i
            break

# Replace all three at once
replacement = stock_code + '\n\n' + sector_code + '\n\n' + fb_code
new_content = content[:s1] + replacement + content[fb_end+1:]

with open(SCANNER, 'w') as f:
    f.write(new_content)

# Verify
total_nan = new_content.count('nan')
print(f"\n✅ Replaced all 3 dictionaries")
print(f"Total 'nan' remaining in file: {total_nan}")

if total_nan == 0:
    print("🎉 COMPLETELY CLEAN!")
else:
    # Show where nan still is
    idx = new_content.find('nan')
    if idx > 0:
        print(f"  First nan at char {idx}: ...{new_content[max(0,idx-30):idx+50]}...")
PYEOF