#!/usr/bin/env python3
"""
update_best_combos.py — 從 backtest 數據生成勝率查找表（雙層：個股 + Sector）
================================================================================
版本：2026-05-25
輸入：
  - backtest_4way_results.csv  （個股級，含 symbol 欄位）
  - backtest_sector_results.csv（Sector 級，無 symbol 欄位）
輸出：
  1. BEST_COMBOS_STOCK — 個股級勝率（key 含 symbol，精確匹配）
  2. BEST_COMBOS_SECTOR — Sector 級勝率（key 不含 symbol，樣本更多）
  3. BEST_COMBOS_FALLBACK — 全市場勝率（無 Sector 差異）
查找順序：
  1. BEST_COMBOS_STOCK（個股自己的歷史）
  2. BEST_COMBOS_SECTOR（同行業平均）
  3. BEST_COMBOS_FALLBACK（全市場平均）
"""

import pandas as pd
from collections import defaultdict

# ── 讀取數據 ────────────────────────────────────────────────────────────────

stock_df = pd.read_csv('/Users/aiagent/projects/dashboard/backtest_4way_results.csv')
sector_df = pd.read_csv('/Users/aiagent/projects/dashboard/backtest_sector_results.csv')

print(f"個股級 CSV：{len(stock_df)} 組合，欄位：{list(stock_df.columns)}")
print(f"Sector 級 CSV：{len(sector_df)} 組合，欄位：{list(sector_df.columns)}")

# ── 建立個股級查找表 ────────────────────────────────────────────────────────

stock_combos = {}
for _, r in stock_df.iterrows():
    pat = str(r['matched_pattern']) if pd.notna(r['matched_pattern']) else 'None'
    key = (r['symbol'], r['sector'], r['signal'], pat,
           'Y' if r['volume_confirmed'] else 'N',
           'Y' if r['has_pattern'] else 'N')
    stock_combos[key] = {
        'win_rate': float(r['win_rate']),
        'avg_return': float(r['avg_return']),
        'count': int(r['count'])
    }

# ── 建立 Sector 級查找表 ───────────────────────────────────────────────────

sector_combos = {}
for _, r in sector_df.iterrows():
    pat = str(r['matched_pattern']) if pd.notna(r['matched_pattern']) else 'None'
    key = (r['sector'], r['signal'], pat,
           'Y' if r['volume_confirmed'] else 'N',
           'Y' if r['has_pattern'] else 'N')
    sector_combos[key] = {
        'win_rate': float(r['win_rate']),
        'avg_return': float(r['avg_return']),
        'count': int(r['count'])
    }

# ── 建立 Fallback 級（所有 Sector 平均）───────────────────────────────────

fb_agg = defaultdict(lambda: {'wr_sum': 0.0, 'ret_sum': 0.0, 'count': 0})
for _, r in sector_df.iterrows():
    pat = str(r['matched_pattern']) if pd.notna(r['matched_pattern']) else 'None'
    key = (r['signal'], pat,
           'Y' if r['volume_confirmed'] else 'N',
           'Y' if r['has_pattern'] else 'N')
    fb_agg[key]['wr_sum'] += float(r['win_rate']) * int(r['count'])
    fb_agg[key]['ret_sum'] += float(r['avg_return']) * int(r['count'])
    fb_agg[key]['count'] += int(r['count'])

fallback_combos = {}
for key, agg in fb_agg.items():
    if agg['count'] >= 5:
        fallback_combos[key] = {
            'win_rate': agg['wr_sum'] / agg['count'],
            'avg_return': agg['ret_sum'] / agg['count'],
            'count': agg['count']
        }

print(f"\n個股級：{len(stock_combos)} 組合")
print(f"Sector 級：{len(sector_combos)} 組合")
print(f"Fallback 級：{len(fallback_combos)} 組合")

# ── 打印統計 ────────────────────────────────────────────────────────────────

print("\n" + "=" * 90)
print("📊 個股級高勝率（勝率 ≥ 60%，n ≥ 10）")
print("=" * 90)
top_stock = [
    (k, v) for k, v in stock_combos.items()
    if v['win_rate'] >= 0.60 and v['count'] >= 10
]
top_stock.sort(key=lambda x: -x[1]['win_rate'])
for key, v in top_stock[:15]:
    sym, sec, sig, pat, vol, has_pat = key
    print(f"  {sym:<7} {sec:<20} {sig:<28} {pat:<18} vol={vol} pat={has_pat}  "
          f"{v['win_rate']:>6.1%}  {v['avg_return']:>+7.2%}  n={v['count']}")

print("\n" + "=" * 90)
print("📊 Sector 級高勝率（勝率 ≥ 60%，n ≥ 10）")
print("=" * 90)
top_sec = [
    (k, v) for k, v in sector_combos.items()
    if v['win_rate'] >= 0.60 and v['count'] >= 10
]
top_sec.sort(key=lambda x: -x[1]['win_rate'])
for key, v in top_sec[:15]:
    sec, sig, pat, vol, has_pat = key
    print(f"  {sec:<22} {sig:<28} {pat:<18} vol={vol} pat={has_pat}  "
          f"{v['win_rate']:>6.1%}  {v['avg_return']:>+7.2%}  n={v['count']}")

# ── 生成寫入 signal_scanner.py 的代碼 ─────────────────────────────────────

def make_lines(combo_dict, key_format):
    """生成 dict 字面量行，key_format 決定 key 組裝方式"""
    lines = []
    for key, data in sorted(combo_dict.items(),
                            key=lambda x: (str(x[0][0]), str(x[0][1]),
                                          str(x[0][2]) if len(x[0]) > 2 else '',
                                          bool(x[0][-1]) if isinstance(x[0][-1], bool) else
                                          x[0][-1] == 'Y')):
        k_str = key_format(key)
        lines.append(
            f"    ({k_str}): ({data['win_rate']:.4f}, {data['avg_return']:.4f}, {data['count']}),"
        )
    return lines

# Stock key: (symbol, sector, signal, pattern, vol, has_pat)
def stock_key_fmt(k):
    sym, sec, sig, pat, vol, has_pat = k
    return f"{repr(sym)}, {repr(sec)}, {repr(sig)}, {repr(pat)}, {repr(vol)}, {repr(has_pat)}"

# Sector key: (sector, signal, pattern, vol, has_pat)
def sector_key_fmt(k):
    sec, sig, pat, vol, has_pat = k
    return f"{repr(sec)}, {repr(sig)}, {repr(pat)}, {repr(vol)}, {repr(has_pat)}"

# Fallback key: (signal, pattern, vol, has_pat)
def fallback_key_fmt(k):
    sig, pat, vol, has_pat = k
    return f"{repr(sig)}, {repr(pat)}, {repr(vol)}, {repr(has_pat)}"

stock_lines = make_lines(stock_combos, stock_key_fmt)
sector_lines = make_lines(sector_combos, sector_key_fmt)
fb_lines = make_lines(fallback_combos, fallback_key_fmt)

stock_code = "BEST_COMBOS_STOCK = {\n" + "\n".join(stock_lines) + "\n}"
sector_code = "BEST_COMBOS_SECTOR = {\n" + "\n".join(sector_lines) + "\n}"
fb_code = "BEST_COMBOS_FALLBACK = {\n" + "\n".join(fb_lines) + "\n}"

# ── 寫入 signal_scanner.py（放在 BEST_COMBOS 之後，lookup_win_rate_4d 之前）──

insert_code = f"""
# ═══════════════════════════════════════════════════════════════════════════
# 雙層勝率查找表（2026-05-25：個股級 + Sector 級 + Fallback）
# ═══════════════════════════════════════════════════════════════════════════

{stock_code}

{sector_code}

{fb_code}

# 查找順序：
#   1. BEST_COMBOS_STOCK（個股級，key 含 symbol）
#   2. BEST_COMBOS_SECTOR（Sector 級，key 不含 symbol）
#   3. BEST_COMBOS_FALLBACK（全市場，無 Sector 差異）
"""

with open('/Users/aiagent/projects/dashboard/signal_scanner.py', 'r') as f:
    content = f.read()

# 在 lookup_win_rate_4d 函數前插入
func_marker = "def lookup_win_rate_4d"
if func_marker in content:
    parts = content.split(func_marker, 1)
    content = parts[0] + insert_code + func_marker + parts[1]
else:
    content += insert_code

with open('/Users/aiagent/projects/dashboard/signal_scanner.py', 'w') as f:
    f.write(content)

print("\n✅ 已寫入 signal_scanner.py")
print(f"   BEST_COMBOS_STOCK:    {len(stock_combos)} 組合")
print(f"   BEST_COMBOS_SECTOR:   {len(sector_combos)} 組合")
print(f"   BEST_COMBOS_FALLBACK: {len(fallback_combos)} 組合")
