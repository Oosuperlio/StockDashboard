#!/usr/bin/env python3
"""
backtest_4way.py — 四維回測：Sector × Signal × Pattern確認 × 成交量確認
======================================================================
目標：生成 signal_scanner.py 所需的 BEST_COMBOS 勝率數據

維度：
  1. sector        — 行業（Utilities, Financials, IT, ...）
  2. signal       — 指標信號（BB 跌破下軌, RSI 超賣, ...）
  3. has_pattern  — 形態確認（True/False）
  4. volume_confirmed — 成交量確認（True/False）

產出：backtest_4way_results.csv
"""

import sys
import os
import warnings
warnings.filterwarnings('ignore')

from pathlib import Path
from collections import defaultdict
from typing import List, Tuple, Dict

import pandas as pd
import numpy as np
import requests
from io import StringIO

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import duckdb
from indicator_calculator import calculate_all_indicators
from indicator_signals import (
    detect_rsi_signals, detect_macd_signals, detect_kdj_signals,
    detect_ema_signals, detect_bb_signals
)
from pattern_detector import (
    Pattern, detect_doji, detect_hammer, detect_shooting_star,
    detect_morning_star, detect_evening_star, detect_engulfing,
    detect_harami, detect_support_resistance, detect_flag,
    detect_triangle
)

# ── 參數 ──────────────────────────────────────────────────────────────
FORWARD_DAYS = 5
THRESHOLD = 0.02
MIN_SIGNALS = 5
MIN_PATTERN_CONFIDENCE = 0.5
VOL_MA_PERIOD = 20
VOL_SPIKE_TODAY = 1.5
VOL_SPIKE_NEXT = 1.2

BULLISH_INDICATORS = {
    ('RSI', 'RSI 超賣區域 (30)'),
    ('RSI', 'RSI 維持超賣'),
    ('BB', 'BB 跌破下軌 (超賣)'),
    ('MACD', 'MACD 金叉 (空頭區)'),
    ('MACD', 'MACD 突破 0 軸'),
    ('KDJ', 'KDJ 超賣區金叉'),
    ('EMA', 'EMA 黃金交叉 (20 上穿 50)'),
    ('EMA', '價格突破 EMA20'),
}

BEARISH_INDICATORS = {
    ('RSI', 'RSI 超買區域 (70)'),
    ('RSI', 'RSI 維持超買'),
    ('BB', 'BB 突破上軌 (超買)'),
    ('MACD', 'MACD 死叉 (空頭區)'),
    ('MACD', 'MACD 跌破 0 軸'),
    ('KDJ', 'KDJ 超買區死叉'),
    ('EMA', 'EMA 死亡交叉 (20 下穿 50)'),
    ('EMA', '價格跌破 EMA20'),
}

BULLISH_PATTERNS = {
    'Support', 'Morning Star', 'Bullish Engulfing',
    'Bull Flag', 'Hammer', 'Ascending Triangle', 'Bullish Harami'
}
BEARISH_PATTERNS = {
    'Resistance', 'Evening Star', 'Bearish Engulfing',
    'Bear Flag', 'Shooting Star', 'Descending Triangle', 'Bearish Harami'
}


# ── Sector 映射 ──────────────────────────────────────────────────────

def fetch_sp500_sectors():
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
    resp = requests.get(url, headers=headers, timeout=15)
    tables = pd.read_html(StringIO(resp.text))
    df = tables[0][['Symbol', 'GICS Sector']].rename(columns={'Symbol': 'ticker', 'GICS Sector': 'sector'})
    df['ticker'] = df['ticker'].str.strip()
    return df

def fetch_hsi_sectors():
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    url = 'https://en.wikipedia.org/wiki/Hang_Seng_Index'
    resp = requests.get(url, headers=headers, timeout=15)
    tables = pd.read_html(StringIO(resp.text))
    for t in tables:
        cols = [str(c) for c in t.columns.tolist()]
        if any('Ticker' in c or 'Sub-index' in c for c in cols):
            df = t.copy()
            df.columns = [str(c) for c in df.columns]
            ticker_col = [c for c in df.columns if 'Ticker' in c][0]
            sector_col = [c for c in df.columns if 'Sub-index' in c][0]
            def convert_hk(tk):
                tk = str(tk).replace('SEHK:\xa0', '').replace('SEHK:', '').strip()
                return tk.zfill(4) + '.HK'
            df['ticker'] = df[ticker_col].apply(convert_hk)
            df['sector'] = df[sector_col]
            return df[['ticker', 'sector']]
    return pd.DataFrame(columns=['ticker', 'sector'])

def load_constituents(market):
    if market == 'sp500':
        path = Path(__file__).parent / 'data' / 'constituents_sp500.txt'
    else:
        path = Path(__file__).parent / 'data' / 'constituents_hsi.txt'
    with open(path, 'r') as f:
        return [line.strip() for line in f if line.strip()]

def get_sector_map():
    sp = fetch_sp500_sectors()
    hsi = fetch_hsi_sectors()
    combined = pd.concat([sp, hsi], ignore_index=True)
    return dict(zip(combined['ticker'], combined['sector']))


# ── 形態索引 ──────────────────────────────────────────────────────────

class PatternIndex:
    def __init__(self, pattern):
        self.pattern = pattern
    def covers(self, idx):
        return idx in self.pattern.indices

def build_pattern_index(df):
    df = df.copy()
    df['vol_ma20'] = df['volume'].rolling(VOL_MA_PERIOD, min_periods=10).mean()

    bullish_index = defaultdict(list)
    bearish_index = defaultdict(list)

    for idx in range(5, len(df)):
        for detector in [
            lambda i: detect_doji(df, i), lambda i: detect_hammer(df, i),
            lambda i: detect_shooting_star(df, i), lambda i: detect_morning_star(df, i),
            lambda i: detect_evening_star(df, i), lambda i: detect_engulfing(df, i),
            lambda i: detect_harami(df, i),
        ]:
            try:
                p = detector(idx)
                if p and p.confidence >= MIN_PATTERN_CONFIDENCE:
                    pi = PatternIndex(p)
                    for i in p.indices:
                        if p.direction == 'bullish' and p.name in BULLISH_PATTERNS:
                            bullish_index[i].append(pi)
                        elif p.direction == 'bearish' and p.name in BEARISH_PATTERNS:
                            bearish_index[i].append(pi)
            except Exception:
                pass

    for detector in [detect_support_resistance, detect_flag, detect_triangle]:
        try:
            patterns = detector(df)
            for p in patterns:
                if p.confidence < MIN_PATTERN_CONFIDENCE:
                    continue
                pi = PatternIndex(p)
                for i in p.indices:
                    if p.direction == 'bullish' and p.name in BULLISH_PATTERNS:
                        bullish_index[i].append(pi)
                    elif p.direction == 'bearish' and p.name in BEARISH_PATTERNS:
                        bearish_index[i].append(pi)
        except Exception:
            pass

    return bullish_index, bearish_index, df


# ── 數據加載 ──────────────────────────────────────────────────────────

def load_stock_data(symbol):
    db_path = Path(__file__).parent / 'data' / 'prices.ddb'
    conn = duckdb.connect(str(db_path), read_only=True)
    df = pd.read_sql_query("""
        SELECT trade_date as date, symbol, open, high, low, close, volume
        FROM stock_prices WHERE symbol = ? ORDER BY trade_date ASC
    """, conn, params=(symbol,))
    conn.close()
    if df.empty:
        return df
    df['date'] = pd.to_datetime(df['date'])
    df.set_index('date', inplace=True)
    return df


# ── 單股回測 ──────────────────────────────────────────────────────────

def backtest_stock(symbol, sector):
    """回測單一股票，返回所有 trade 記錄（含 4 維 tag）"""
    df = load_stock_data(symbol)
    if df.empty or len(df) < 60:
        return []

    df = calculate_all_indicators(df)
    bullish_index, bearish_index, df = build_pattern_index(df)

    trades = []

    for idx in range(30, len(df)):
        # 成交量確認
        vol_today_ok = vol_next_ok = False
        if idx + 1 < len(df):
            vol_today = df['volume'].iloc[idx]
            vol_ma = df['vol_ma20'].iloc[idx]
            vol_next = df['volume'].iloc[idx + 1]
            vol_ma_next = df['vol_ma20'].iloc[idx + 1]
            if vol_ma > 0:
                vol_today_ok = vol_today >= vol_ma * VOL_SPIKE_TODAY
            if vol_ma_next > 0:
                vol_next_ok = vol_next >= vol_ma_next * VOL_SPIKE_NEXT
        vol_confirmed = vol_today_ok and vol_next_ok

        # 指標信號
        all_ind_signals = []
        all_ind_signals.extend(detect_rsi_signals(df, idx))
        all_ind_signals.extend(detect_macd_signals(df, idx))
        all_ind_signals.extend(detect_kdj_signals(df, idx))
        all_ind_signals.extend(detect_ema_signals(df, idx))
        all_ind_signals.extend(detect_bb_signals(df, idx))

        bull_pis = bullish_index.get(idx, [])
        bear_pis = bearish_index.get(idx, [])

        for ind_sig in all_ind_signals:
            ind_key = (ind_sig.indicator, ind_sig.name)
            is_bull = ind_key in BULLISH_INDICATORS and ind_sig.signal_type == 'bullish'
            is_bear = ind_key in BEARISH_INDICATORS and ind_sig.signal_type == 'bearish'
            if not (is_bull or is_bear):
                continue

            direction = ind_sig.signal_type
            matched_pattern = None
            matched_conf = 0.0

            if direction == 'bullish':
                for pi in bull_pis:
                    if pi.pattern.confidence > matched_conf:
                        matched_pattern = pi.pattern.name
                        matched_conf = pi.pattern.confidence
            else:
                for pi in bear_pis:
                    if pi.pattern.confidence > matched_conf:
                        matched_pattern = pi.pattern.name
                        matched_conf = pi.pattern.confidence

            has_pattern = matched_pattern is not None

            # 計算回報
            if idx + FORWARD_DAYS >= len(df):
                continue
            entry = df['close'].iloc[idx]
            exit_p = df['close'].iloc[idx + FORWARD_DAYS]
            ret = (exit_p - entry) / entry
            if direction == 'bearish':
                ret = -ret

            trades.append({
                'symbol': symbol,
                'sector': sector,
                'indicator': ind_sig.indicator,
                'signal': ind_sig.name,
                'direction': direction,
                'has_pattern': has_pattern,
                'volume_confirmed': vol_confirmed,
                'return': ret,
                'is_success': ret > THRESHOLD,
            })

    return trades


# ── 主程序 ────────────────────────────────────────────────────────────

def main():
    print("=" * 80)
    print("🔍 四維回測：Sector × Signal × Pattern確認 × 成交量確認")
    print("=" * 80)

    sector_map = get_sector_map()
    print(f"\n已載入 {len(sector_map)} 檔股票的 sector 映射")

    all_trades = []

    for market in ['sp500', 'hsi']:
        tickers = load_constituents(market)
        market_name = 'S&P 500' if market == 'sp500' else 'HSI'
        print(f"\n📂 回測 {market_name} ({len(tickers)} 檔)...")

        for i, sym in enumerate(tickers):
            sector = sector_map.get(sym, 'Unknown')
            trades = backtest_stock(sym, sector)
            all_trades.extend(trades)

            if (i + 1) % 50 == 0:
                print(f"  ... 已處理 {i+1} 隻，累計 {len(all_trades)} 個信號")

        print(f"  ✅ {market_name} 完成：{len(tickers)} 檔")

    print(f"\n總信號數：{len(all_trades)}")

    if not all_trades:
        print("❌ 無數據")
        return

    # ── 四維聚合 ──────────────────────────────────────────────────
    agg = defaultdict(lambda: {'count': 0, 'successes': 0, 'total_return': 0.0})

    for t in all_trades:
        key = (t['sector'], t['indicator'], t['signal'], t['direction'],
               t['has_pattern'], t['volume_confirmed'])
        agg[key]['count'] += 1
        agg[key]['total_return'] += t['return']
        if t['is_success']:
            agg[key]['successes'] += 1

    rows = []
    for (sector, indicator, signal, direction, has_pattern, vol_confirmed), stats in agg.items():
        if stats['count'] < MIN_SIGNALS:
            continue
        wr = stats['successes'] / stats['count']
        avg_ret = stats['total_return'] / stats['count']
        rows.append({
            'sector': sector,
            'indicator': indicator,
            'signal': signal,
            'direction': direction,
            'has_pattern': has_pattern,
            'volume_confirmed': vol_confirmed,
            'count': stats['count'],
            'win_rate': wr,
            'avg_return': avg_ret,
        })

    df = pd.DataFrame(rows)

    if df.empty:
        print("❌ 聚合後無足夠樣本")
        return

    # 計算相對於「無形態 + 無成交量」的提升
    df['improvement'] = 0.0
    for idx, row in df.iterrows():
        base_rows = df[
            (df['signal'] == row['signal']) &
            (df['has_pattern'] == False) &
            (df['volume_confirmed'] == False) &
            (df['direction'] == row['direction']) &
            (df['sector'] == row['sector'])
        ]
        if not base_rows.empty:
            base_wr = base_rows.iloc[0]['win_rate']
            df.loc[idx, 'improvement'] = row['win_rate'] - base_wr

    # 保存結果
    out_path = Path(__file__).parent / 'backtest_4way_results.csv'
    df.to_csv(out_path, index=False)
    print(f"\n💾 已保存：{out_path}（{len(df)} 個組合）")

    # ── 打印高勝率組合（≥65%，有成交量，有形態）────────────────────
    print("\n" + "=" * 100)
    print("🔥 高勝率組合（形態確認 + 成交量確認，勝率 ≥ 60%）")
    print("=" * 100)
    top = df[
        (df['has_pattern'] == True) &
        (df['volume_confirmed'] == True) &
        (df['win_rate'] >= 0.60) &
        (df['count'] >= 10) &
        (df['direction'] == 'bullish')
    ].sort_values('win_rate', ascending=False)

    print(f"\n{'Sector':<25} {'Signal':<28} {'Count':>6} {'勝率':>8} {'平均回報':>10} {'提升':>8}")
    print("-" * 100)
    for _, r in top.iterrows():
        vol_icon = '🔔' if r['volume_confirmed'] else '⚪'
        pat_icon = '✅' if r['has_pattern'] else '⚪'
        print(f"{r['sector']:<25} {r['signal']:<28} {r['count']:>6} {r['win_rate']:>8.1%} {r['avg_return']:>+10.2%} {r['improvement']:>+8.1%}")

    # ── 打印：加入成交量後勝率提升前10名 ─────────────────────────
    print("\n\n" + "=" * 100)
    print("📈 成交量確認帶來的勝率提升 TOP 10（形態確認=True）")
    print("=" * 100)
    vol_lift = df[
        (df['has_pattern'] == True) &
        (df['count'] >= 10) &
        (df['direction'] == 'bullish')
    ].copy()

    # 計算 volume lift per signal+sector
    vol_lift['vol_lift'] = 0.0
    for idx, row in vol_lift.iterrows():
        no_vol = vol_lift[
            (vol_lift['signal'] == row['signal']) &
            (vol_lift['sector'] == row['sector']) &
            (vol_lift['has_pattern'] == row['has_pattern']) &
            (vol_lift['volume_confirmed'] == False)
        ]
        if not no_vol.empty:
            vol_lift.loc[idx, 'vol_lift'] = row['win_rate'] - no_vol.iloc[0]['win_rate']

    top_lift = vol_lift[vol_lift['volume_confirmed'] == True].nlargest(10, 'vol_lift')
    print(f"\n{'Sector':<25} {'Signal':<28} {'Count':>6} {'有量勝率':>8} {'無量勝率':>8} {'提升':>8}")
    print("-" * 100)
    for _, r in top_lift.iterrows():
        no_vol_row = vol_lift[
            (vol_lift['signal'] == r['signal']) &
            (vol_lift['sector'] == r['sector']) &
            (vol_lift['has_pattern'] == r['has_pattern']) &
            (vol_lift['volume_confirmed'] == False)
        ]
        no_vol_wr = no_vol_row.iloc[0]['win_rate'] if not no_vol_row.empty else 0
        print(f"{r['sector']:<25} {r['signal']:<28} {r['count']:>6} {r['win_rate']:>8.1%} {no_vol_wr:>8.1%} {r['vol_lift']:>+8.1%}")

    print("\n✅ 四維回測完成！")


if __name__ == '__main__':
    main()
