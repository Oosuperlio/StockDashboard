"""
backtest_indicators.py — 技術指標勝率回測引擎
=============================================
功能:
  1. 加載股價數據 (DuckDB: S&P 500 / HSI)
  2. 計算所有技術指標
  3. 檢測信號並記錄信號發生日期
  4. 在信號發生後 N 天內檢驗價格變化
  5. 輸出各指標信號的勝率排行榜

用法:
  python backtest_indicators.py              # 全量回測
  python backtest_indicators.py --sp500      # 僅 S&P 500
  python backtest_indicators.py --hsi         # 僅 HSI
  python backtest_indicators.py --stock AAPL  # 單一股票
"""

import sys
import os
import argparse
import duckdb
from pathlib import Path
from collections import defaultdict

import pandas as pd
import numpy as np

# 導入本地模組
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from indicator_calculator import calculate_all_indicators
from indicator_signals import (
    detect_rsi_signals, detect_macd_signals, detect_kdj_signals,
    detect_ema_signals, detect_bb_signals
)


# ─────────────────────────────────────────────
# 參數配置
# ─────────────────────────────────────────────
FORWARD_DAYS = 5       # 信號出現後 5 天內檢驗
THRESHOLD = 0.02       # 2% 價格變動門檻
MIN_SIGNALS = 10       # 最少信號數量才納入統計

# 指標 → 信號映射 (用於聚合)
INDICATOR_SIGNALS = {
    'RSI': {
        'bullish': ['RSI 超賣區域 (30)', 'RSI 維持超賣', 'RSI 上穿 50 中性線'],
        'bearish': ['RSI 超買區域 (70)', 'RSI 維持超買', 'RSI 下穿 50 中性線'],
    },
    'MACD': {
        'bullish': ['MACD 金叉 (多頭區)', 'MACD 金叉 (空頭區)', 'MACD 突破 0 軸'],
        'bearish': ['MACD 死叉 (空頭區)', 'MACD 死叉 (多頭區)', 'MACD 跌破 0 軸'],
    },
    'KDJ': {
        'bullish': ['KDJ 超賣區金叉', 'KDJ 金叉', 'KDJ J 值極低 (<0)'],
        'bearish': ['KDJ 超買區死叉', 'KDJ 死叉', 'KDJ J 值極高 (>100)'],
    },
    'EMA': {
        'bullish': ['EMA 多頭排列 (20>50>200)', 'EMA 黃金交叉 (20 上穿 50)',
                    '價格突破 EMA20'],
        'bearish': ['EMA 空頭排列 (20<50<200)', 'EMA 死亡交叉 (20 下穿 50)',
                    '價格跌破 EMA20'],
    },
    'BB': {
        'bullish': ['BB 跌破下軌 (超賣)'],
        'bearish': ['BB 突破上軌 (超買)'],
    }
}

# ─────────────────────────────────────────────
# 數據加載
# ─────────────────────────────────────────────

def load_constituents(market: str) -> list:
    """加載成分股列表"""
    if market == 'sp500':
        path = Path(__file__).parent / 'data' / 'constituents_sp500.txt'
    elif market == 'hsi':
        path = Path(__file__).parent / 'data' / 'constituents_hsi.txt'
    else:
        raise ValueError(f"Unknown market: {market}")

    with open(path, 'r') as f:
        tickers = [line.strip() for line in f if line.strip()]
    return tickers


def load_stock_data(symbol: str, market: str) -> pd.DataFrame:
    """從 DuckDB 數據庫加載股價數據"""
    db_path = Path(__file__).parent / 'data' / 'prices.ddb'
    conn = duckdb.connect(str(db_path), read_only=True)

    query = """
        SELECT trade_date as date, symbol, open, high, low, close, volume
        FROM stock_prices
        WHERE symbol = ?
        ORDER BY trade_date ASC
    """
    df = pd.read_sql_query(query, conn, params=(symbol,))
    conn.close()

    if df.empty:
        return df

    df['date'] = pd.to_datetime(df['date'])
    df.set_index('date', inplace=True)
    return df


# ─────────────────────────────────────────────
# 信號檢測
# ─────────────────────────────────────────────

def detect_signals_for_stock(df: pd.DataFrame) -> list:
    """
    檢測一檔股票所有指標信號
    返回: [(date, signal_name, direction, confidence), ...]
    """
    # 計算指標
    df = calculate_all_indicators(df)

    signals = []
    for idx in range(20, len(df)):  # 至少需要 20 根 K 線計算指標
        date = df.index[idx]

        # RSI
        for sig in detect_rsi_signals(df, idx):
            signals.append((date, sig.name, sig.signal_type, sig.confidence, 'RSI'))

        # MACD
        for sig in detect_macd_signals(df, idx):
            signals.append((date, sig.name, sig.signal_type, sig.confidence, 'MACD'))

        # KDJ
        for sig in detect_kdj_signals(df, idx):
            signals.append((date, sig.name, sig.signal_type, sig.confidence, 'KDJ'))

        # EMA
        for sig in detect_ema_signals(df, idx):
            signals.append((date, sig.name, sig.signal_type, sig.confidence, 'EMA'))

        # BB
        for sig in detect_bb_signals(df, idx):
            signals.append((date, sig.name, sig.signal_type, sig.confidence, 'BB'))

    return signals


# ─────────────────────────────────────────────
# 勝率計算
# ─────────────────────────────────────────────

def backtest_signal(df: pd.DataFrame, signal_date_idx: int,
                    direction: str, forward_days: int = FORWARD_DAYS) -> dict:
    """
    回測單一信號
    direction: 'bullish' 或 'bearish'
    """
    if signal_date_idx + forward_days >= len(df):
        return None

    entry_price = df['close'].iloc[signal_date_idx]
    exit_price = df['close'].iloc[signal_date_idx + forward_days]

    ret = (exit_price - entry_price) / entry_price

    # bearish 信號：符號翻轉（做空）
    if direction == 'bearish':
        ret = -ret

    is_success = ret > THRESHOLD

    return {
        'entry_price': entry_price,
        'exit_price': exit_price,
        'return': ret,
        'is_success': is_success
    }


def aggregate_results(results: list) -> pd.DataFrame:
    """
    聚合回測結果為排行榜
    """
    from collections import defaultdict

    agg = defaultdict(lambda: {'count': 0, 'successes': 0, 'total_return': 0.0})

    # 確保 results 是 dict 的列表
    if isinstance(results, pd.DataFrame):
        records = results.to_dict('records')
    else:
        records = results

    for r in records:
        key = (r['indicator'], r['signal_name'], r['direction'])
        agg[key]['count'] += 1
        agg[key]['total_return'] += r['return']
        if r['is_success']:
            agg[key]['successes'] += 1

    rows = []
    for (indicator, signal_name, direction), stats in agg.items():
        if stats['count'] < MIN_SIGNALS:
            continue
        win_rate = stats['successes'] / stats['count']
        avg_return = stats['total_return'] / stats['count']
        rows.append({
            'indicator': indicator,
            'signal': signal_name,
            'direction': direction,
            'count': stats['count'],
            'successes': stats['successes'],
            'failures': stats['count'] - stats['successes'],
            'win_rate': win_rate,
            'avg_return': avg_return,
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    df = df.sort_values('win_rate', ascending=False).reset_index(drop=True)
    return df


# ─────────────────────────────────────────────
# 主回測流程
# ─────────────────────────────────────────────

def backtest_market(market: str, tickers: list = None) -> pd.DataFrame:
    """
    回測整個市場
    """
    if tickers is None:
        tickers = load_constituents(market)

    all_results = []
    processed = 0
    skipped = 0

    for symbol in tickers:
        df = load_stock_data(symbol, market)
        if df.empty:
            skipped += 1
            continue

        # 確保有足夠數據
        if len(df) < 60:
            skipped += 1
            continue

        processed += 1

        try:
            signals = detect_signals_for_stock(df)
        except Exception as e:
            print(f"  ⚠ {symbol}: 錯誤 - {e}")
            continue

        # 為每個信號執行回測
        for (date, signal_name, direction, confidence, indicator) in signals:
            date_idx = df.index.get_loc(date)
            result = backtest_signal(df, date_idx, direction)

            if result is None:
                continue

            all_results.append({
                'symbol': symbol,
                'date': date,
                'indicator': indicator,
                'signal_name': signal_name,
                'direction': direction,
                'confidence': confidence,
                **result
            })

        if processed % 50 == 0:
            print(f"  ... 已處理 {processed} 隻股票")

    print(f"\n完成: 處理 {processed} 隻, 跳過 {skipped} 隻, 共 {len(all_results)} 個信號")

    if not all_results:
        return pd.DataFrame()

    return aggregate_results(pd.DataFrame(all_results))


def backtest_indicator_summary(results_df: pd.DataFrame) -> pd.DataFrame:
    """
    按指標聚合（不看具體信號，只看指標級別勝率）
    """
    if results_df.empty:
        return pd.DataFrame()

    agg = results_df.groupby('indicator').agg(
        total_count=('count', 'sum'),
        total_successes=('successes', 'sum'),
        avg_win_rate=('win_rate', 'mean'),
        best_signal=('win_rate', 'idxmax'),
        best_win_rate=('win_rate', 'max')
    ).reset_index()

    agg['overall_win_rate'] = agg['total_successes'] / agg['total_count']
    return agg.sort_values('overall_win_rate', ascending=False)


# ─────────────────────────────────────────────
# 輸出格式化
# ─────────────────────────────────────────────

def print_results(results_df: pd.DataFrame, title: str = "技術指標勝率排行榜"):
    """格式化打印結果"""
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")

    if results_df.empty:
        print("  無足夠數據")
        return

    # TOP 20 信號
    print("\n📊 信號勝率排行榜 (TOP 20):")
    print(f"  {'排名':<4} {'指標':<6} {'信號':<30} {'方向':<8} {'次數':<6} {'勝率':<8} {'平均回報':<10}")
    print(f"  {'-'*4} {'-'*6} {'-'*30} {'-'*8} {'-'*6} {'-'*8} {'-'*10}")

    for i, row in results_df.head(20).iterrows():
        rank = results_df.index.get_loc(i) + 1
        emoji = "🟢" if row['direction'] == 'bullish' else "🔴"
        print(f"  {rank:<4} {row['indicator']:<6} {row['signal']:<30} {emoji} {row['direction']:<6} "
              f"{row['count']:<6} {row['win_rate']:.1%}     {row['avg_return']:+.2%}")


def print_indicator_summary(summary_df: pd.DataFrame):
    """打印指標級別摘要"""
    print("\n📈 指標勝率摘要:")
    print(f"  {'指標':<8} {'總信號數':<10} {'總勝數':<8} {'整體勝率':<10} {'平均勝率':<10} {'最佳信號':<30}")
    print(f"  {'-'*8} {'-'*10} {'-'*8} {'-'*10} {'-'*10} {'-'*30}")

    for _, row in summary_df.iterrows():
        best_signal = row['best_signal']
        if isinstance(best_signal, tuple):
            best_signal = f"{best_signal[0]}: {best_signal[1][:25]}"
        print(f"  {row['indicator']:<8} {row['total_count']:<10} {row['total_successes']:<8} "
              f"{row['overall_win_rate']:.1%}     {row['avg_win_rate']:.1%}     {best_signal}")


# ─────────────────────────────────────────────
# 主程序
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='技術指標勝率回測')
    parser.add_argument('--sp500', action='store_true', help='僅回測 S&P 500')
    parser.add_argument('--hsi', action='store_true', help='僅回測 HSI')
    parser.add_argument('--stock', type=str, help='單一股票代碼')
    parser.add_argument('--output', type=str, help='輸出 CSV 路徑')
    args = parser.parse_args()

    markets = []
    if args.sp500:
        markets.append('sp500')
    elif args.hsi:
        markets.append('hsi')
    else:
        markets = ['sp500', 'hsi']

    all_results = []

    for market in markets:
        market_name = 'S&P 500' if market == 'sp500' else 'HSI'
        print(f"\n🔍 開始回測 {market_name}...")

        if args.stock:
            tickers = [args.stock]
        else:
            tickers = None

        results = backtest_market(market, tickers)
        if not results.empty:
            results['market'] = market_name
            all_results.append(results)

    if not all_results:
        print("無回測結果")
        return

    combined = pd.concat(all_results, ignore_index=True)

    # 打印結果
    for market in combined['market'].unique():
        mdf = combined[combined['market'] == market]
        print_results(mdf, f"{market} 技術指標勝率排行榜")

        summary = backtest_indicator_summary(mdf)
        print_indicator_summary(summary)

    # 保存結果
    if args.output:
        combined.to_csv(args.output, index=False)
        print(f"\n💾 結果已保存至: {args.output}")
    else:
        output_path = Path(__file__).parent / 'backtest_indicator_results.csv'
        combined.to_csv(output_path, index=False)
        print(f"\n💾 結果已保存至: {output_path}")


if __name__ == '__main__':
    main()
