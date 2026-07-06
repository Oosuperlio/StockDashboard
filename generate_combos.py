#!/usr/bin/env python3
"""從 backtest_4way.py 的結果 CSV 生成 BEST_COMBOS 字典並寫入 signal_scanner.py"""
import pandas as pd
from pathlib import Path

BASE_DIR = Path(__file__).parent
STOCK_CSV = BASE_DIR / 'backtest_4way_results.csv'
SECTOR_CSV = BASE_DIR / 'backtest_sector_results.csv'
SCANNER_FILE = BASE_DIR / 'signal_scanner.py'
MIN_SAMPLES = 5

def format_key(key_tuple):
    """Format a tuple as Python code"""
    parts = []
    for item in key_tuple:
        if isinstance(item, bool):
            parts.append('True' if item else 'False')
        elif isinstance(item, str):
            escaped = item.replace("'", "\\'")
            parts.append(f"'{escaped}'")
        else:
            parts.append(repr(item))
    return '(' + ', '.join(parts) + ')'

def format_val(val_tuple):
    """Format value tuple"""
    wr, avg_ret, count = val_tuple
    return f"({wr}, {avg_ret}, {count})"

def generate_dict_code(items, dict_name):
    """Generate Python dictionary code"""
    lines = [f"{dict_name} = {{"]
    for item in items:
        key_str = format_key(item['key'])
        val_str = format_val(item['val'])
        lines.append(f"    {key_str}: {val_str},")
    lines.append("}")
    return '\n'.join(lines)

# Load data
stock_df = pd.read_csv(STOCK_CSV)
sector_df = pd.read_csv(SECTOR_CSV)

# Build stock dictionary
stock_items = []
for _, r in stock_df.iterrows():
    if r['count'] < MIN_SAMPLES:
        continue
    vol = 'Y' if r['volume_confirmed'] else 'N'
    pat_flag = 'Y' if r['has_pattern'] else 'N'
    stock_items.append({
        'key': (r['symbol'], r['sector'], r['signal'], 
                r['matched_pattern'] if pd.notna(r['matched_pattern']) else 'None', 
                vol, pat_flag),
        'val': (float(round(r['win_rate'], 4)), float(round(r['avg_return'], 4)), int(r['count']))
    })

# Build sector dictionary
sector_items = []
for _, r in sector_df.iterrows():
    if r['count'] < MIN_SAMPLES:
        continue
    vol = 'Y' if r['volume_confirmed'] else 'N'
    pat_flag = 'Y' if r['has_pattern'] else 'N'
    sector_items.append({
        'key': (r['sector'], r['signal'], 
                r['matched_pattern'] if pd.notna(r['matched_pattern']) else 'None', 
                vol, pat_flag),
        'val': (float(round(r['win_rate'], 4)), float(round(r['avg_return'], 4)), int(r['count']))
    })

# Build fallback dictionary (aggregate across all sectors)
fallback_agg = {}
for _, r in sector_df.iterrows():
    if r['count'] < MIN_SAMPLES:
        continue
    vol = 'Y' if r['volume_confirmed'] else 'N'
    pat_flag = 'Y' if r['has_pattern'] else 'N'
    fb_key = (r['signal'], r['matched_pattern'] if pd.notna(r['matched_pattern']) else 'None', vol, pat_flag)
    if fb_key not in fallback_agg:
        fallback_agg[fb_key] = {'count': 0, 'success_sum': 0.0, 'ret_sum': 0.0}
    agg = fallback_agg[fb_key]
    agg['count'] += int(r['count'])
    agg['success_sum'] += r['win_rate'] * r['count']
    agg['ret_sum'] += r['avg_return'] * r['count']

fallback_items = []
for fb_key, agg in fallback_agg.items():
    if agg['count'] < MIN_SAMPLES:
        continue
    wr = agg['success_sum'] / agg['count']
    avg_ret = agg['ret_sum'] / agg['count']
    fallback_items.append({
        'key': fb_key,
        'val': (round(wr, 4), round(avg_ret, 4), agg['count'])
    })

print(f"STOCK: {len(stock_items)}  SECTOR: {len(sector_items)}  FALLBACK: {len(fallback_items)}")

# Generate dictionary code
stock_code = generate_dict_code(stock_items, "BEST_COMBOS_STOCK")
sector_code = generate_dict_code(sector_items, "BEST_COMBOS_SECTOR")
fallback_code = generate_dict_code(fallback_items, "BEST_COMBOS_FALLBACK")

# Find the last occurrence boundaries in signal_scanner.py
with open(SCANNER_FILE, 'r') as f:
    content = f.read()

# Find all BEST_COMBOS_STOCK declarations
import re
stock_starts = [m.start() for m in re.finditer(r'^BEST_COMBOS_STOCK\s*=\s*\{', content, re.MULTILINE)]
sector_starts = [m.start() for m in re.finditer(r'^BEST_COMBOS_SECTOR\s*=\s*\{', content, re.MULTILINE)]
fallback_starts = [m.start() for m in re.finditer(r'^BEST_COMBOS_FALLBACK\s*=\s*\{', content, re.MULTILINE)]

print(f"Found {len(stock_starts)} BEST_COMBOS_STOCK, {len(sector_starts)} BEST_COMBOS_SECTOR, {len(fallback_starts)} BEST_COMBOS_FALLBACK")

# Also find the comment before the last copy
header_marker = "# 雙層勝率查找表"
header_positions = [m.start() for m in re.finditer(header_marker, content)]
print(f"Header marker positions: {header_positions}")

# Read the scanner file
lines = content.split('\n')

# Find the lines for the LAST copies
def find_last_dict_range(lines, dict_name):
    """Find the line range of the last occurrence of a dictionary"""
    start_patterns = {
        'BEST_COMBOS_FALLBACK': '# 雙層勝率查找表',
        'BEST_COMBOS_STOCK': 'BEST_COMBOS_STOCK = {',
        'BEST_COMBOS_SECTOR': 'BEST_COMBOS_SECTOR = {',
    }
    
    # Find all start lines
    starts = []
    pat = start_patterns.get(dict_name, dict_name)
    for i, line in enumerate(lines):
        if pat in line:
            starts.append(i)
    
    if not starts:
        return None
    
    last_start = starts[-1]
    
    # Find the closing brace for this dict
    brace_depth = 0
    in_dict = False
    for i in range(last_start, len(lines)):
        line = lines[i]
        if dict_name in line and '{' in line:
            in_dict = True
            brace_depth = line.count('{') - line.count('}')
            continue
        if in_dict:
            brace_depth += line.count('{') - line.count('}')
            if brace_depth <= 0:
                return last_start, i
    
    return None

# Replace the FALLBACK dictionary (last instance)
fb_range = find_last_dict_range(lines, 'BEST_COMBOS_FALLBACK')
print(f"FALLBACK range: {fb_range}")
if fb_range:
    # Replace lines fb_range[0] through fb_range[1]
    # Keep the header comment
    header_line = lines[fb_range[0]]
    new_lines = lines[:fb_range[0]] + [header_line] + fallback_code.split('\n')[1:] + lines[fb_range[1]+1:]
    lines = new_lines
    print(f"Replaced FALLBACK at lines {fb_range[0]}-{fb_range[1]}")

# Now find the STOCK dictionary between header and FALLBACK
# The header is now at the replaced position, find STOCK dicts before that
starts = []
for i, line in enumerate(lines):
    if 'BEST_COMBOS_STOCK = {' in line:
        starts.append(i)

print(f"Remaining BEST_COMBOS_STOCK count: {len(starts)}")

# Replace the last STOCK
if len(starts) >= 2:
    stock_start = starts[-1]
    brace_depth = 0
    in_dict = False
    for i in range(stock_start, len(lines)):
        line = lines[i]
        if 'BEST_COMBOS_STOCK = {' in line:
            in_dict = True
            brace_depth = line.count('{') - line.count('}')
            continue
        if in_dict:
            brace_depth += line.count('{') - line.count('}')
            if brace_depth <= 0:
                new_lines = lines[:stock_start] + stock_code.split('\n') + lines[i+1:]
                lines = new_lines
                print(f"Replaced STOCK at line {stock_start}")
                break

# Find SECTOR between the remaining STOCK and the header+fallback
# Actually let me just find the last SECTOR
starts = []
for i, line in enumerate(lines):
    if 'BEST_COMBOS_SECTOR = {' in line:
        starts.append(i)

print(f"Remaining BEST_COMBOS_SECTOR count: {len(starts)}")

if len(starts) >= 2:
    sector_start = starts[-1]
    in_dict = False
    brace_depth = 0
    for i in range(sector_start, len(lines)):
        line = lines[i]
        if 'BEST_COMBOS_SECTOR = {' in line:
            in_dict = True
            brace_depth = line.count('{') - line.count('}')
            continue
        if in_dict:
            brace_depth += line.count('{') - line.count('}')
            if brace_depth <= 0:
                new_lines = lines[:sector_start] + sector_code.split('\n') + lines[i+1:]
                lines = new_lines
                print(f"Replaced SECTOR at line {sector_start}")
                break

# Write back
output = '\n'.join(lines)
with open(SCANNER_FILE, 'w') as f:
    f.write(output)

print(f"\nWritten {len(output):,} chars to signal_scanner.py")
print(f"New file lines: {len(lines)}")
