"""
backtesting.py — 形態勝率回測排行榜
====================================
跑完後輸出所有形態的勝率排行榜（跨股票彙總）

用法：
  python backtesting.py              # 全部 6 支股票
  python backtesting.py 3968.HK       # 單支股票
  python backtesting.py 3968.HK 0700.HK  # 兩支股票

依賴：yfinance, pandas, numpy（與 dashboard 共用 requirements.txt）
"""

import sys
import pandas as pd
import numpy as np
import duckdb
from collections import defaultdict
from datetime import datetime
from typing import Optional

# ── 加入 dashboard 路徑，这样可以直接 import pattern_detector ──
sys.path.insert(0, "/Users/aiagent/projects/dashboard")
from pattern_detector import detect_all_patterns

# ── 參數 ──────────────────────────────────────────────────────
TICKERS = ["3968.HK", "0700.HK", "0005.HK", "JPM", "PG", "PEP"]
DAYS = 365
FORWARD_DAYS = 5          # 信號日 → N 天後結算
MOVE_THRESHOLD = 0.01     # 價格移動 ≥ 1% 才算有效
MIN_CONFIDENCE = 0.30     # 低於此信心度的信號直接排除
DB_PATH = "/Users/aiagent/projects/dashboard/data/prices.ddb"

# ── 已禁用的形態（按用戶意願）────────────────────────────────
DISABLED = {
    "Head & Shoulders",
    "Inverse H&S",
    "Double Top",
    "Double Bottom",
}

# ── 數據取得（從 DuckDB）──────────────────────────────────────
def fetch_data(ticker: str, days: int) -> pd.DataFrame:
    """從 DuckDB 讀取股票歷史數據，整理成標準格式"""
    conn = duckdb.connect(DB_PATH, read_only=True)
    try:
        df = conn.execute("""
            SELECT trade_date, symbol, open, high, low, close, volume
            FROM stock_prices
            WHERE symbol = ?
            ORDER BY trade_date
        """, [ticker]).fetchdf()
    finally:
        conn.close()

    if df.empty:
        print(f"  [⚠️ {ticker}] DB 中無數據")
        return df

    # DuckDB 的 trade_date 是 datetime，pattern_detector 期望 date
    df = df.rename(columns={"trade_date": "date"})

    # 確保 ohlcv 為 float
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = df[col].astype(float)

    print(f"  [{ticker}] {len(df)} 行 | {df['date'].iloc[0].date()} → {df['date'].iloc[-1].date()}")
    return df

# ── 結算函式 ──────────────────────────────────────────────────
def forward_return(df: pd.DataFrame, idx: int, direction: str) -> Optional[float]:
    """
    計算形態出現日之後 FORWARD_DAYS 天的價格回報。
    direction='bullish' → 期待上漲，回報取正向
    direction='bearish' → 期待下跌，回報取負向（順勢做空）
    direction='neutral' → 不結算
    """
    if direction == "neutral":
        return None
    if idx + FORWARD_DAYS >= len(df):
        return None
    entry = df["close"].iloc[idx]
    exit_price = df["close"].iloc[idx + FORWARD_DAYS]
    ret = (exit_price - entry) / entry
    # bearish pattern：翻轉符號，視為「做空方向的成功與否」
    return ret if direction == "bullish" else -ret

def is_win(ret: float) -> bool:
    """ret 已按方向翻轉，這裡只看正負"""
    return ret >= MOVE_THRESHOLD

# ── 主回測邏輯 ────────────────────────────────────────────────
def backtest_ticker(ticker: str, days: int = DAYS) -> dict:
    """對單一股票執行形態回測，回傳 {(name, direction): {count, wins}}"""
    df = fetch_data(ticker, days)
    patterns = detect_all_patterns(df)

    # 按 (name, direction) 分組收集結算結果
    stats: dict = defaultdict(lambda: {"wins": 0, "count": 0, "returns": []})

    for p in patterns:
        if p.name in DISABLED:
            continue
        if p.confidence < MIN_CONFIDENCE:
            continue
        key = (p.name, p.direction)
        for idx in p.indices:
            ret = forward_return(df, idx, p.direction)
            if ret is None:
                continue
            stats[key]["count"] += 1
            stats[key]["returns"].append(ret)
            if is_win(ret):
                stats[key]["wins"] += 1

    return stats

def aggregate(results_by_ticker: list[dict]) -> list[dict]:
    """
    跨股票彙總：相同 (name, direction) 的勝率、平均回報、置信度
    """
    agg: dict = defaultdict(lambda: {"wins": 0, "count": 0, "returns": [], "confidence_sum": 0, "confidence_n": 0})

    for stats in results_by_ticker:
        for (name, direction), s in stats.items():
            if s["count"] == 0:
                continue
            agg[(name, direction)]["wins"] += s["wins"]
            agg[(name, direction)]["count"] += s["count"]
            agg[(name, direction)]["returns"].extend(s["returns"])
            agg[(name, direction)]["confidence_sum"] += sum(s["returns"])  # placeholder
            agg[(name, direction)]["confidence_n"] += s["count"]

    rows = []
    for (name, direction), s in agg.items():
        if s["count"] == 0:
            continue
        win_rate = s["wins"] / s["count"] * 100
        avg_ret = np.mean(s["returns"]) * 100
        rows.append({
            "pattern": name,
            "direction": direction,
            "count": s["count"],
            "wins": s["wins"],
            "losses": s["count"] - s["wins"],
            "win_rate": win_rate,
            "avg_return": avg_ret,
        })

    rows.sort(key=lambda x: -x["win_rate"])
    return rows

# ── 輸出格式化 ────────────────────────────────────────────────
def print_leaderboard(rows: list[dict], tickers: list[str]):
    emoji_dir = {"bullish": "🟢", "bearish": "🔴", "neutral": "🟡"}
    stars = lambda wr: "★" if wr >= 60 else ("☆" if wr >= 50 else "  ")

    print("\n" + "=" * 76)
    print(f"  形態勝率排行榜  |  前瞻窗口: {FORWARD_DAYS}天  |  移動門檻: {MOVE_THRESHOLD*100:.0f}%")
    print(f"  標的: {' / '.join(tickers)}")
    print("=" * 76)
    header = f"  {'形態':<24} {'方向':<7} {'次數':>6} {'勝':>5} {'負':>5} {'勝率':>8} {'平均回報':>10}  {'評級'}"
    print(header)
    print("  " + "-" * 74)

    for r in rows:
        dir_emoji = emoji_dir.get(r["direction"], "⚪")
        star = stars(r["win_rate"])
        # 評級
        if r["win_rate"] >= 70:
            rating = "優秀"
        elif r["win_rate"] >= 60:
            rating = "良好"
        elif r["win_rate"] >= 50:
            rating = "一般"
        elif r["win_rate"] >= 40:
            rating = "偏弱"
        else:
            rating = "危險"

        print(
            f"  {r['pattern']:<24} {dir_emoji}{r['direction']:<6} "
            f"{r['count']:>6} {r['wins']:>5} {r['losses']:>5} "
            f"{r['win_rate']:>7.1f}% {r['avg_return']:>+9.2f}%  {star} {rating}"
        )

    print("  " + "-" * 74)
    print(f"  ★ = 勝率≥60%  ☆ = 勝率≥50%")
    print(f"  低信心信號（<{MIN_CONFIDENCE*100:.0f}%）已過濾 | 已排除形態：{', '.join(sorted(DISABLED))}")
    print(f"  運行時間: {datetime.now().strftime('%H:%M:%S')}")
    print("=" * 76)

    # ── 附加：各股票細項 ──────────────────────────────────────
    print("\n  【各股票形態分佈】")
    print(f"  {'標的':<10} {'形態總數':>8} {'覆蓋形態種類':>14}")
    print("  " + "-" * 36)

# ── 入口 ──────────────────────────────────────────────────────
if __name__ == "__main__":
    # 解析命令行參數
    if len(sys.argv) > 1:
        targets = sys.argv[1:]
    else:
        targets = TICKERS

    print(f"\n🚀 開始回測形態辨識 | 標的: {targets} | 天數: {DAYS}")
    print("-" * 50)

    results_by_ticker = []
    for ticker in targets:
        try:
            stats = backtest_ticker(ticker)
            results_by_ticker.append(stats)
        except Exception as e:
            print(f"  ⚠️ [{ticker}] 回測失敗: {e}")

    if not results_by_ticker:
        print("❌ 沒有任何股票成功跑完，請檢查網絡或 ticker 格式。")
        sys.exit(1)

    rows = aggregate(results_by_ticker)
    print_leaderboard(rows, targets)

    # 儲存結果到 CSV（可選）
    csv_path = "/Users/aiagent/projects/dashboard/backtest_results.csv"
    if rows:
        df_out = pd.DataFrame(rows)
        df_out.to_csv(csv_path, index=False)
        print(f"\n📄 結果已儲存: {csv_path}")
