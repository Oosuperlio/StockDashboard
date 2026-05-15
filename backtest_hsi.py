"""
HSI 成分股形態勝率回測
覆蓋全部 HSI 成分股，聚合所有形態的歷史勝率
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
        ticker_results[(name, direction)] = {
            "count": len(valid),
            "successes": successes,
            "avg_return": np.mean(valid),
            "confidence": confidence,
        }
    return ticker_results

# ── 主回測 ────────────────────────────────────────────────
print(f"📊 HSI 形態回測開始 | {len(tickers)} 隻股票 | 前瞻窗口: {FORWARD_DAYS}天 | 門檻: {THRESHOLD*100}%")
print("-" * 60)

all_results = {}
for ticker in tickers:
    try:
        res = backtest_ticker(ticker)
        if res:
            all_results[ticker] = res
    except Exception as e:
        print(f"  ⚠️ {ticker}: {e}")

print(f"✅ 完成！成功: {len(all_results)} 隻")

# ── 聚合 ─────────────────────────────────────────────────
agg = defaultdict(lambda: {"count": 0, "successes": 0, "returns": [], "confidence": 0.0})
for ticker, results in all_results.items():
    for (name, direction), data in results.items():
        key = (name, direction)
        agg[key]["count"] += data["count"]
        agg[key]["successes"] += data["successes"]
        agg[key]["returns"].append(data["avg_return"])
        agg[key]["confidence"] = data["confidence"]

final = []
for (name, direction), data in agg.items():
    if data["count"] == 0:
        continue
    win_rate = data["successes"] / data["count"] * 100
    avg_ret = np.mean(data["returns"]) * 100
    final.append({
        "pattern": name,
        "direction": direction,
        "count": data["count"],
        "successes": data["successes"],
        "failures": data["count"] - data["successes"],
        "win_rate": win_rate,
        "avg_return": avg_ret,
        "confidence": data["confidence"],
    })

final.sort(key=lambda x: -x["win_rate"])

# ── 輸出 ─────────────────────────────────────────────────
print("\n" + "=" * 80)
print(f"形態勝率排行榜 | 覆蓋 {len(all_results)} 隻 HSI 成分股")
print("=" * 80)
header = f"{'形態':<22} {'方向':<8} {'總次數':>6} {'成功':>6} {'失敗':>6} {'勝率':>7} {'平均回報':>9} {'置信':>6}"
print(header)
print("-" * 80)
for r in final:
    mark = " ★" if r["win_rate"] >= 60 else (" ☆" if r["win_rate"] >= 50 else "")
    print(
        f"{r['pattern']:<22} {r['direction']:<8} "
        f"{r['count']:>6} {r['successes']:>6} {r['failures']:>6} "
        f"{r['win_rate']:>6.1f}% {r['avg_return']:>+8.2f}% {r['confidence']:>5.2f}{mark}"
    )
print("-" * 80)
print(f"★ = 勝率≥60%  ☆ = 勝率≥50%")

# ── 儲存 ─────────────────────────────────────────────────
out = "backtest_results_hsi.csv"
with open(out, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["pattern","direction","count","successes","failures","win_rate","avg_return","confidence"])
    w.writeheader()
    w.writerows(final)
print(f"\n💾 已儲存: {out}")
