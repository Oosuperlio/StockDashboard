"""
HSI 成分股形態詳細回測表
行：成分股代碼
列：每種形態的 (數量, 勝率)
"""
import sys
import csv
from collections import defaultdict
import numpy as np
import duckdb
from pattern_detector import detect_all_patterns

sys.path.insert(0, "/Users/aiagent/projects/dashboard")

# ── 參數 ──────────────────────────────────────────────
FORWARD_DAYS = 5
THRESHOLD = 0.02
DISABLED = {"Head & Shoulders", "Inverse H&S", "Double Top", "Double Bottom"}

# ── 讀取 HSI 成分股 ─────────────────────────────────
with open("data/constituents_hsi.txt") as f:
    tickers = [line.strip() for line in f if line.strip()]

# ── 從 DuckDB 讀取股價數據 ────────────────────────────────
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

# ── 形態回測核心邏輯 ─────────────────────────────────────
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

# ── 對單隻股票運行形態回測 ────────────────────────────────
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

# ── 主回測 ────────────────────────────────────────────────
print(f"📊 HSI 形態詳細回測 | {len(tickers)} 隻股票")
print("-" * 60)

all_ticker_results = {}
for ticker in tickers:
    try:
        res = backtest_ticker(ticker)
        if res:
            all_ticker_results[ticker] = res
    except Exception as e:
        print(f"  ⚠️ {ticker}: {e}")

print(f"✅ 完成！成功: {len(all_ticker_results)} 隻")

# ── 收集所有形態鍵 ───────────────────────────────────────
all_pattern_keys = set()
for ticker_res in all_ticker_results.values():
    all_pattern_keys.update(ticker_res.keys())

# 排序：先按名稱，再按方向
pattern_keys_sorted = sorted(all_pattern_keys, key=lambda x: (x[0], x[1]))

# ── 建立 CSV ──────────────────────────────────────────────
# 欄位：ticker, 然後每個形態一組 (count_形態方向, winrate_形態方向)
fieldnames = ["ticker"]
for pk in pattern_keys_sorted:
    name, direction = pk
    # 簡化名稱
    short_name = name.replace(" ", "_")[:15]
    fieldnames.append(f"cnt_{short_name}_{direction[:3]}")
    fieldnames.append(f"wr_{short_name}_{direction[:3]}")

out = "backtest_hsi_detail.csv"
with open(out, "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()

    for ticker in tickers:
        row = {"ticker": ticker}
        ticker_res = all_ticker_results.get(ticker, {})
        for pk in pattern_keys_sorted:
            name, direction = pk
            short_name = name.replace(" ", "_")[:15]
            cnt_key = f"cnt_{short_name}_{direction[:3]}"
            wr_key = f"wr_{short_name}_{direction[:3]}"
            if pk in ticker_res:
                row[cnt_key] = ticker_res[pk]["count"]
                row[wr_key] = round(ticker_res[pk]["win_rate"], 1)
            else:
                row[cnt_key] = 0
                row[wr_key] = 0.0
        writer.writerow(row)

print(f"💾 詳細表格已儲存: {out}")

# ── 同時輸出一個易讀的摘要版 ────────────────────────────────
# 找出所有形態鍵（只用名稱，合併方向）
all_pattern_names = sorted(set(pk[0] for pk in all_pattern_keys))

print(f"\n{'代碼':<10}", end="")
for pn in all_pattern_names:
    print(f"{pn[:8]:>9}", end="")
print()
print("-" * (10 + 9 * len(all_pattern_names)))

for ticker in tickers:
    ticker_res = all_ticker_results.get(ticker, {})
    print(f"{ticker:<10}", end="")
    for pn in all_pattern_names:
        # 合併該形態所有方向的總數
        total = sum(v["count"] for k, v in ticker_res.items() if k[0] == pn)
        print(f"{total:>9}", end="")
    print()

print(f"\n📌 上表為每隻股票各形態的出現次數（不含勝率）")
print(f"💾 完整表格（含數量+勝率）已儲存: {out}")
