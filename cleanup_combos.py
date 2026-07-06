#!/usr/bin/env python3
"""清理 signal_scanner.py 中所有舊的 BEST_COMBOS 副本，只保留最後一版"""
import re
from pathlib import Path

FILE = Path(__file__).parent / 'signal_scanner.py'
with open(FILE, 'r') as f:
    lines = f.readlines()

# 找到三個字典的起止位置
dict_ranges = {}  # dict_name -> [(start_line, end_line), ...]

for name in ['BEST_COMBOS_STOCK', 'BEST_COMBOS_SECTOR', 'BEST_COMBOS_FALLBACK']:
    ranges = []
    for i, line in enumerate(lines):
        if f'{name} = {{' in line:
            start = i
            depth = 0
            in_dict = False
            for j in range(i, len(lines)):
                l = lines[j]
                if f'{name} = {{' in l:
                    in_dict = True
                    depth = l.count('{') - l.count('}')
                    continue
                if in_dict:
                    depth += l.count('{') - l.count('}')
                    if depth <= 0:
                        ranges.append((start, j))
                        break
    dict_ranges[name] = ranges
    print(f'{name}: {len(ranges)} copies (last at line {ranges[-1][0]+1})')

# 只保留最後一版（最後一個 range），刪除前面的
lines_to_delete = set()
for name, ranges in dict_ranges.items():
    if len(ranges) > 1:
        for start, end in ranges[:-1]:
            for i in range(start, end + 1):
                lines_to_delete.add(i)
            print(f'  Delete {name} at lines {start+1}-{end+1}')

# 也刪除相關的註釋行（空行、header）
# 從後往前刪除，保持索引有效
new_lines = [l for i, l in enumerate(lines) if i not in lines_to_delete]

# 清理多餘的連續空行
cleaned = []
prev_blank = False
for line in new_lines:
    is_blank = line.strip() == ''
    if is_blank and prev_blank:
        continue
    cleaned.append(line)
    prev_blank = is_blank

with open(FILE, 'w') as f:
    f.writelines(cleaned)

print(f'\nDone! Lines: {len(lines)} -> {len(cleaned)} (removed {len(lines)-len(cleaned)})')
