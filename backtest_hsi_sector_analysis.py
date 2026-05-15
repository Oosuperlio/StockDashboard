"""
HSI 形態勝率 × Sector / Sub-Industry 分析
"""
import sys
import csv
import requests
import pandas as pd
from io import StringIO
from collections import defaultdict
import numpy as np
import duckdb
from pattern_detector import detect_all_patterns

sys.path.insert(0, "/Users/aiagent/projects/dashboard")

# ── 參數 ──────────────────────────────────────────────
FORWARD_DAYS = 5
THRESHOLD = 0.02
DISABLED = {"Head & Shoulders", "Inverse H&S", "Double Top", "Double Bottom"}

# ── 1. 從 Wikipedia 獲取 HSI Sector 分類 ─────────────────────────
def fetch_hsi_sectors():
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    url = 'https://en.wikipedia.org/wiki/Hang_Seng_Index'
    resp = requests.get(url, headers=headers, timeout=15)
    tables = pd.read_html(StringIO(resp.text))
    t = tables[6]  # HSI constituents table
    t = t.rename(columns={'Ticker': 'ticker', 'Name': 'name', 'Sub-index': 'sector'})

    # 轉換 ticker 格式：SEHK: 5 → 0005.HK
    def convert_ticker(tk):
        tk = tk.replace('SEHK:\xa0', '').strip()
        return tk.zfill(4) + '.HK'

    t['ticker'] = t['ticker'].apply(convert_ticker)
    return t[['ticker', 'name', 'sector']]

# ── 2. 讀取 HSI 成分股列表 ───────────────────────────────────────
def load_hsi_tickers():
    with open("data/constituents_hsi.txt") as f:
        return [line.strip() for line in f if line.strip()]

# ── 3. 從 DuckDB 讀取股價數據 ────────────────────────────────
def load_prices_from_db(ticker):
    con = duckdb.connect("data/prices.ddb")
    df = con.sql(f"""
        SELECT trade_date, open, high, low, close, volume
        FROM stock_prices
        WHERE symbol = '{ticker}'
        ORDER BY trade_date
    """).df()
    con.close()
    if df.empty or len(df) < 60:
        return None
    df.columns = [c.lower() for c in df.columns]
    df["trade_date"] = df["trade_date"].astype("datetime64[ns]")
    return df.reset_index(drop=True)

# ── 4. 形態回測核心邏輯 ─────────────────────────────────────
def forward_return(df, idx, direction):
    if idx + FORWARD_DAYS >= len(df):
        return np.nan
    entry = df["close"].iloc[idx]
    exit_price = df["close"].iloc[idx + FORWARD_DAYS]
    ret = (exit_price - entry) / entry
    return ret if direction == "bullish" else -ret

def is_success(ret, confidence):
    if np.isnan(ret):
        return False
    adj_threshold = THRESHOLD * (1.1 - confidence * 0.2)
    return ret > adj_threshold

# ── 5. 對單隻股票運行形態回測 ────────────────────────────────
def backtest_ticker(ticker):
    df = load_prices_from_db(ticker)
    if df is None:
        return {}

    all_patterns = detect_all_patterns(df)
    groups = defaultdict(list)
    for p in all_patterns:
        groups[(p.name, p.direction)].extend(p.indices)

    ticker_results = {}
    for (name, direction), all_idx in groups.items():
        if name in DISABLED:
            continue
        c_list = [p.confidence for p in all_patterns if p.name == name]
        confidence = np.mean(c_list) if c_list else 0.6

        rets = [forward_return(df, i, direction) for i in all_idx]
        valid = [r for r in rets if not np.isnan(r)]
        if not valid:
            continue

        successes = sum(is_success(r, confidence) for r in valid)
        win_rate = successes / len(valid) * 100
        ticker_results[(name, direction)] = {
            "count": len(valid),
            "successes": successes,
            "win_rate": win_rate,
        }
    return ticker_results

# ── 主程式 ────────────────────────────────────────────────
print("📊 HSI Sector 分析開始")
print("-" * 60)

# 讀取 sector 數據
hsi_sectors = fetch_hsi_sectors()
print(f"✅ 取得 {len(hsi_sectors)} 隻 HSI 的 sector 分類")
print(f"   Sectors: {hsi_sectors['sector'].value_counts().to_dict()}")

hsi_tickers = load_hsi_tickers()
print(f"📋 成分股列表: {len(hsi_tickers)} 隻")

# 建立 ticker → sector 映射
ticker_to_sector = {}
for _, row in hsi_sectors.iterrows():
    ticker_to_sector[row['ticker']] = row['sector']

# ── 運行回測 ────────────────────────────────────────────────
print(f"\n🔄 開始形態回測...")
all_ticker_results = {}
for ticker in hsi_tickers:
    try:
        res = backtest_ticker(ticker)
        if res:
            all_ticker_results[ticker] = res
    except Exception as e:
        pass

print(f"✅ 完成！{len(all_ticker_results)} 隻股票有形態數據")

# ── 6. 聚合：按 Sector ─────────────────────────────────────────────
print("\n" + "=" * 80)
print("📊 HSI SECTOR 形態勝率排行榜")
print("=" * 80)

sector_pattern_agg = defaultdict(lambda: {"count": 0, "successes": 0})

for ticker, results in all_ticker_results.items():
    sector = ticker_to_sector.get(ticker, "Unknown")
    for (name, direction), data in results.items():
        key = (sector, name, direction)
        sector_pattern_agg[key]["count"] += data["count"]
        sector_pattern_agg[key]["successes"] += data["successes"]

sector_summary = defaultdict(lambda: {"count": 0, "successes": 0})
for (sector, name, direction), data in sector_pattern_agg.items():
    sector_summary[sector]["count"] += data["count"]
    sector_summary[sector]["successes"] += data["successes"]

sector_win_rates = []
for sector, data in sector_summary.items():
    wr = data["successes"] / data["count"] * 100 if data["count"] > 0 else 0
    sector_win_rates.append({
        "sector": sector,
        "count": data["count"],
        "successes": data["successes"],
        "win_rate": wr,
    })
sector_win_rates.sort(key=lambda x: -x["win_rate"])

print(f"\n{'Sector':<25} {'總形態數':>8} {'成功':>6} {'失敗':>6} {'勝率':>7}")
print("-" * 60)
for s in sector_win_rates:
    print(f"{s['sector']:<25} {s['count']:>8} {s['successes']:>6} {s['count']-s['successes']:>6} {s['win_rate']:>6.1f}%")

# ── 7. 按 Sector × 形態 ─────────────────────────────────────────────
print("\n" + "=" * 80)
print("📊 SECTOR × 形態 勝率詳表")
print("=" * 80)

all_sectors = [s['sector'] for s in sector_win_rates]
all_pattern_keys = sorted(set((k[1], k[2]) for k in sector_pattern_agg.keys()))

# 建立交叉表
cross = {}
for sector in all_sectors:
    cross[sector] = {}
    for (pattern, direction) in all_pattern_keys:
        key = (sector, pattern, direction)
        if key in sector_pattern_agg and sector_pattern_agg[key]["count"] >= 5:
            c = sector_pattern_agg[key]["count"]
            s = sector_pattern_agg[key]["successes"]
            cross[sector][(pattern, direction)] = (c, s / c * 100)

# 打印每個 sector 的形態勝率
for sector in all_sectors:
    sector_data = cross[sector]
    if not sector_data:
        continue
    sorted_items = sorted(sector_data.items(), key=lambda x: -x[1][1])
    print(f"\n{'─'*60}")
    print(f"📌 {sector} (共 {sector_summary[sector]['count']} 個形態)")
    print(f"{'形態':<22} {'方向':<8} {'數量':>5} {'勝率':>7}")
    print(f"{'─'*60}")
    for (pattern, direction), (cnt, wr) in sorted_items:
        mark = " ★" if wr >= 60 else (" ☆" if wr >= 50 else "")
        print(f"{pattern:<22} {direction:<8} {cnt:>5} {wr:>6.1f}%{mark}")

# ── 8. 形態 × Sector 交叉表 ─────────────────────────────────────────
print("\n" + "=" * 80)
print("📊 形態 × SECTOR 勝率交叉表")
print("=" * 80)

all_pattern_names = sorted(set(k[1] for k in sector_pattern_agg.keys()))

# 打印表頭
header = f"{'形態':<22}"
for sector in all_sectors:
    header += f"{sector[:10]:>12}"
print(header)
print("-" * (22 + 12 * len(all_sectors)))

for pattern in all_pattern_names:
    row = f"{pattern:<22}"
    for sector in all_sectors:
        # 合併該形態所有方向的數據
        total_c = sum(sector_pattern_agg[(sector, pattern, d)]["count"]
                      for d in ["bullish", "bearish", "neutral"]
                      if (sector, pattern, d) in sector_pattern_agg)
        total_s = sum(sector_pattern_agg[(sector, pattern, d)]["successes"]
                      for d in ["bullish", "bearish", "neutral"]
                      if (sector, pattern, d) in sector_pattern_agg)
        if total_c >= 5:
            wr = total_s / total_c * 100
            row += f"{wr:>11.1f}%"
        else:
            row += f"{'N/A':>12}"
    print(row)

# ── 9. 儲存結果 ──────────────────────────────────────────────
sector_csv = "backtest_hsi_sector_summary.csv"
with open(sector_csv, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["sector","count","successes","win_rate"])
    w.writeheader()
    for s in sector_win_rates:
        w.writerow(s)

# Sector × Pattern 詳細
detail_rows = []
for (sector, pattern, direction), data in sector_pattern_agg.items():
    if data["count"] >= 5:
        detail_rows.append({
            "sector": sector,
            "pattern": pattern,
            "direction": direction,
            "count": data["count"],
            "successes": data["successes"],
            "win_rate": data["successes"] / data["count"] * 100,
        })
detail_rows.sort(key=lambda x: -x["win_rate"])

detail_csv = "backtest_hsi_sector_pattern.csv"
with open(detail_csv, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["sector","pattern","direction","count","successes","win_rate"])
    w.writeheader()
    w.writerows(detail_rows)

print(f"\n💾 Sector 排名: {sector_csv}")
print(f"💾 Sector×形態詳細: {detail_csv}")
