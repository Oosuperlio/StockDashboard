"""
形態識別回測：計算各形態的歷史準確率
用法：python backtest_patterns.py [ticker] [days]
"""
import sys
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# ── 參數設定 ──────────────────────────────────────────────
TICKER = sys.argv[1] if len(sys.argv) > 1 else "3968.HK"
DAYS   = int(sys.argv[2]) if len(sys.argv) > 2 else 365
FORWARD_DAYS = 5      # 形態出現後多少天內檢驗
THRESHOLD    = 0.02   # 價格變動門檻（2%）

# ── 形態清單（已禁用）────────────────────────────────────
DISABLED = {"Head & Shoulders", "Inverse H&S", "Double Top", "Double Bottom"}

# ── 導入 pattern_detector（直接copy關鍵邏輯，避免import路徑問題）─────────
sys.path.insert(0, "/Users/aiagent/projects/dashboard")
from pattern_detector import detect_all_patterns, Pattern

# ── 輔助函式 ──────────────────────────────────────────────
def fetch_data(ticker: str, days: int) -> pd.DataFrame:
    df = yf.download(ticker, period=f"{days+30}d", auto_adjust=True, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.reset_index()
    df.columns = [c.lower() if isinstance(c, str) else c for c in df.columns]
    date_col = [c for c in df.columns if "date" in c.lower()][0]
    df["date"] = pd.to_datetime(df[date_col])
    df = df.dropna()
    print(f"[{ticker}] 獲取 {len(df)} 行數據，{df['date'].iloc[0].date()} → {df['date'].iloc[-1].date()}")
    return df

def forward_return(df: pd.DataFrame, idx: int, direction: str) -> float:
    """計算形態出現日之後 FORWARD_DAYS 天的價格回報"""
    if idx + FORWARD_DAYS >= len(df):
        return np.nan
    entry = df["close"].iloc[idx]
    exit_price = df["close"].iloc[idx + FORWARD_DAYS]
    ret = (exit_price - entry) / entry
    return ret if direction == "bullish" else -ret  # bearish pattern：翻轉符號

def is_success(ret: float, confidence: float) -> bool:
    if np.isnan(ret):
        return False
    # 信心度加權：高信心形態門檻稍低
    adj_threshold = THRESHOLD * (1.1 - confidence * 0.2)
    return ret > adj_threshold

# ── 主回測邏輯 ───────────────────────────────────────────
def backtest_pattern(df: pd.DataFrame, pattern_name: str,
                     direction: str, indices: list,
                     confidence: float) -> dict:
    rets = [forward_return(df, i, direction) for i in indices]
    valid = [r for r in rets if not np.isnan(r)]
    if not valid:
        return None
    avg_ret = np.mean(valid)
    successes = sum(is_success(r, confidence) for r in valid)
    return {
        "pattern": pattern_name,
        "direction": direction,
        "count": len(valid),
        "successes": successes,
        "failures": len(valid) - successes,
        "win_rate": successes / len(valid) * 100,
        "avg_return": avg_ret * 100,
        "confidence": confidence,
    }

# ── 收集形態 & 執行回測 ──────────────────────────────────
def run_backtest(df: pd.DataFrame):
    all_patterns = detect_all_patterns(df)

    # 按形態分組（合併不同方向的同名形態）
    from collections import defaultdict
    groups: dict = defaultdict(list)
    for p in all_patterns:
        groups[(p.name, p.direction)].extend(p.indices)

    results = []
    for (name, direction), all_idx in groups.items():
        if name in DISABLED:
            continue
        # 找一個 representative confidence（取平均）
        c_list = [p.confidence for p in all_patterns if p.name == name]
        confidence = np.mean(c_list) if c_list else 0.6
        r = backtest_pattern(df, name, direction, all_idx, confidence)
        if r:
            results.append(r)

    # 排序：win_rate  descending
    results.sort(key=lambda x: -x["win_rate"])

    # 輸出
    print("\n" + "="*70)
    print(f"形態回測結果  |  前瞻窗口: {FORWARD_DAYS}天  |  價格門檻: {THRESHOLD*100}%")
    print(f"標的: {TICKER}  |  數據範圍: ~{DAYS}天")
    print("="*70)
    header = f"{'形態':<22} {'方向':<8} {'次數':>5} {'成功':>5} {'失敗':>5} {'勝率':>7} {'平均回報':>9} {'置信':>6}"
    print(header)
    print("-"*70)
    for r in results:
        mark = " ★" if r["win_rate"] >= 60 else (" ☆" if r["win_rate"] >= 50 else "")
        print(
            f"{r['pattern']:<22} {r['direction']:<8} "
            f"{r['count']:>5} {r['successes']:>5} {r['failures']:>5} "
            f"{r['win_rate']:>6.1f}% {r['avg_return']:>+8.2f}% {r['confidence']:>5.2f}{mark}"
        )
    print("-"*70)
    print(f"★ = 勝率≥60%  ☆ = 勝率≥50%")
    return results

# ── 入口 ────────────────────────────────────────────────
if __name__ == "__main__":
    df = fetch_data(TICKER, DAYS)
    run_backtest(df)
