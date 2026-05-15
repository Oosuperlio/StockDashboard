"""
S&P 500 形態勝率 × Sector / Sub-Industry 分析
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

# ── 1. 從 Wikipedia 獲取 S&P 500 Sector 分類 ─────────────────────────
def fetch_sp500_sectors():
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
    resp = requests.get(url, headers=headers, timeout=15)
    tables = pd.read_html(StringIO(resp.text))
    df = tables[0]
    df = df[['Symbol', 'GICS Sector', 'GICS Sub-Industry']].rename(
        columns={'Symbol': 'ticker', 'GICS Sector': 'sector', 'GICS Sub-Industry': 'subsector'})
    # 清理符號：BRK.B → BRK.B, BF.B → BF.B
    return df

# ── 2. 讀取 S&P 500 成分股列表 ───────────────────────────────────────
def load_sp500_tickers():
    with open("data/constituents_sp500.txt") as f:
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
print("📊 S&P 500 Sector 分析開始")
print("-" * 60)

# 讀取 sector 數據
sp500_sectors = fetch_sp500_sectors()
print(f"✅ 取得 {len(sp500_sectors)} 隻 S&P 500 的 sector 分類")

sp500_tickers = load_sp500_tickers()
print(f"📋 成分股列表: {len(sp500_tickers)} 隻")

# 建立 ticker → sector/subsector 映射
ticker_to_sector = {}
ticker_to_subsector = {}
for _, row in sp500_sectors.iterrows():
    t = row['ticker']
    ticker_to_sector[t] = row['sector']
    ticker_to_subsector[t] = row['subsector']

# ── 運行回測 ────────────────────────────────────────────────
print(f"\n🔄 開始形態回測...")
all_ticker_results = {}
for ticker in sp500_tickers:
    try:
        res = backtest_ticker(ticker)
        if res:
            all_ticker_results[ticker] = res
    except Exception as e:
        pass

print(f"✅ 完成！{len(all_ticker_results)} 隻股票有形態數據")

# ── 6. 聚合：按 Sector ─────────────────────────────────────────────
print("\n" + "=" * 80)
print("📊 SECTOR 形態勝率排行榜")
print("=" * 80)

# {(sector, pattern, direction): {count, successes}}
sector_pattern_agg = defaultdict(lambda: {"count": 0, "successes": 0})

for ticker, results in all_ticker_results.items():
    sector = ticker_to_sector.get(ticker, "Unknown")
    for (name, direction), data in results.items():
        key = (sector, name, direction)
        sector_pattern_agg[key]["count"] += data["count"]
        sector_pattern_agg[key]["successes"] += data["successes"]

# 按 sector 匯總
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

print(f"\n{'Sector':<35} {'總形態數':>8} {'成功':>6} {'失敗':>6} {'勝率':>7}")
print("-" * 70)
for s in sector_win_rates:
    print(f"{s['sector']:<35} {s['count']:>8} {s['successes']:>6} {s['count']-s['successes']:>6} {s['win_rate']:>6.1f}%")

# ── 7. 聚合：按 Sub-Industry ─────────────────────────────────────────
print("\n" + "=" * 80)
print("📊 SUB-INDUSTRY 形態勝率排行榜")
print("=" * 80)

# 重新按 sub-industry 匯總（跨方向）
sub_industry_agg = defaultdict(lambda: {"count": 0, "successes": 0})
for (sector, name, direction), data in sector_pattern_agg.items():
    sub_industry_agg[(sector, name)]["count"] += data["count"]
    sub_industry_agg[(sector, name)]["successes"] += data["successes"]

sub_win_rates = []
for (sector, sub), data in sub_industry_agg.items():
    if data["count"] < 20:  # 太少樣本不具有統計意義
        continue
    wr = data["successes"] / data["count"] * 100 if data["count"] > 0 else 0
    sub_win_rates.append({
        "sector": sector,
        "subsector": sub,
        "count": data["count"],
        "successes": data["successes"],
        "win_rate": wr,
    })
sub_win_rates.sort(key=lambda x: -x["win_rate"])

print(f"\n{'Sector':<20} {'Sub-Industry':<35} {'總數':>6} {'勝率':>7}")
print("-" * 75)
for s in sub_win_rates[:50]:  # 只顯示前50
    print(f"{s['sector']:<20} {s['subsector']:<35} {s['count']:>6} {s['win_rate']:>6.1f}%")

# ── 8. 形態 × Sector 交叉表 ─────────────────────────────────────────
print("\n" + "=" * 80)
print("📊 形態 × SECTOR 勝率交叉表")
print("=" * 80)

# 收集所有形態
all_pattern_names = sorted(set(k[1] for k in sector_pattern_agg.keys()))
all_sectors = sorted(set(sector_win_rates[i]["sector"] for i in range(len(sector_win_rates))))

# 建立交叉表
cross_table = {}
for sector in all_sectors:
    cross_table[sector] = {}
    for pattern in all_pattern_names:
        total_c = sum(sector_pattern_agg[(sector, pattern, d)]["count"] for d in ["bullish", "bearish", "neutral"] if (sector, pattern, d) in sector_pattern_agg)
        total_s = sum(sector_pattern_agg[(sector, pattern, d)]["successes"] for d in ["bullish", "bearish", "neutral"] if (sector, pattern, d) in sector_pattern_agg)
        if total_c >= 10:
            cross_table[sector][pattern] = total_s / total_c * 100
        else:
            cross_table[sector][pattern] = None

# 打印交叉表（選擇關鍵形態）
key_patterns = ["Bull Flag", "Morning Star", "Support", "Resistance", "Evening Star", "Bullish Engulfing", "Bearish Engulfing", "Doji", "Bearish Harami"]
key_patterns = [p for p in key_patterns if p in all_pattern_names]

header = f"{'Sector':<22}" + "".join(f"{p[:10]:>12}" for p in key_patterns)
print("\n" + header)
print("-" * (22 + 12 * len(key_patterns)))
for sector in all_sectors:
    row = f"{sector:<22}"
    for p in key_patterns:
        v = cross_table[sector].get(p)
        if v is not None:
            row += f"{v:>11.1f}%"
        else:
            row += f"{'N/A':>12}"
    print(row)

# ── 9. 儲存結果 ──────────────────────────────────────────────
# Sector 排名
with open("backtest_sector_summary.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["sector","count","successes","win_rate"])
    w.writeheader()
    for s in sector_win_rates:
        w.writerow(s)

# Sub-Industry 排名
with open("backtest_subsector_summary.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["sector","subsector","count","successes","win_rate"])
    w.writeheader()
    for s in sorted(sub_win_rates, key=lambda x: -x["win_rate"]):
        w.writerow(s)

print(f"\n💾 Sector 排名: backtest_sector_summary.csv")
print(f"💾 Sub-Industry 排名: backtest_subsector_summary.csv")
