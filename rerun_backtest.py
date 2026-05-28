#!/usr/bin/env python3
"""
rerun_backtest.py — 分別用 1 年數據及全量數據重新跑 S&P 500 回測
專注比較 TRMB 的 KDJ 超賣區金叉 + Support 形態的 Sector/Stock 勝率
"""
import sys
import os
import warnings
warnings.filterwarnings('ignore')

from pathlib import Path
from collections import defaultdict
import duckdb
import pandas as pd
import numpy as np
import requests
from io import StringIO

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from indicator_calculator import calculate_all_indicators
from indicator_signals import (
    detect_rsi_signals, detect_macd_signals, detect_kdj_signals,
    detect_ema_signals, detect_bb_signals
)
from pattern_detector import (
    Pattern, detect_doji, detect_hammer, detect_shooting_star,
    detect_morning_star, detect_evening_star, detect_engulfing,
    detect_harami, detect_support_resistance, detect_flag, detect_triangle
)

# ── 常數 ──────────────────────────────────────────────────────────────
FORWARD_DAYS = 5
THRESHOLD = 0.02
MIN_SIGNALS = 5
MIN_PATTERN_CONFIDENCE = 0.5
VOL_MA_PERIOD = 20
VOL_SPIKE_TODAY = 1.5
VOL_SPIKE_NEXT = 1.2

BULLISH_INDICATORS = {
    ('RSI', 'RSI 超賣區域 (30)'), ('RSI', 'RSI 維持超賣'),
    ('BB', 'BB 跌破下軌 (超賣)'),
    ('MACD', 'MACD 金叉 (空頭區)'), ('MACD', 'MACD 突破 0 軸'),
    ('KDJ', 'KDJ 超賣區金叉'),
    ('EMA', 'EMA 黃金交叉 (20 上穿 50)'), ('EMA', '價格突破 EMA20'),
}
BEARISH_INDICATORS = {
    ('RSI', 'RSI 超買區域 (70)'), ('RSI', 'RSI 維持超買'),
    ('BB', 'BB 突破上軌 (超買)'),
    ('MACD', 'MACD 死叉 (空頭區)'), ('MACD', 'MACD 跌破 0 軸'),
    ('KDJ', 'KDJ 超買區死叉'),
    ('EMA', 'EMA 死亡交叉 (20 下穿 50)'), ('EMA', '價格跌破 EMA20'),
}
BULLISH_PATTERNS = {'Support', 'Morning Star', 'Bullish Engulfing', 'Bull Flag', 'Hammer', 'Ascending Triangle', 'Bullish Harami'}
BEARISH_PATTERNS = {'Resistance', 'Evening Star', 'Bearish Engulfing', 'Bear Flag', 'Shooting Star', 'Descending Triangle', 'Bearish Harami'}


def fetch_sp500_sectors():
    headers = {'User-Agent': 'Mozilla/5.0'}
    url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
    resp = requests.get(url, headers=headers, timeout=15)
    tables = pd.read_html(StringIO(resp.text))
    df = tables[0][['Symbol', 'GICS Sector']].rename(columns={'Symbol': 'ticker', 'GICS Sector': 'sector'})
    df['ticker'] = df['ticker'].str.strip()
    return df


def load_constituents(market):
    path = Path(__file__).parent / 'data' / f'constituents_{market}.txt'
    with open(path) as f:
        return [line.strip() for line in f if line.strip()]


class PatternIndex:
    def __init__(self, pattern): self.pattern = pattern
    def covers(self, idx): return idx in self.pattern.indices


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


def load_stock_data(symbol, start_date=None):
    db_path = Path(__file__).parent / 'data' / 'prices.ddb'
    conn = duckdb.connect(str(db_path), read_only=True)
    if start_date:
        df = pd.read_sql_query("""
            SELECT trade_date as date, symbol, open, high, low, close, volume
            FROM stock_prices WHERE symbol = ? AND trade_date >= ? ORDER BY trade_date ASC
        """, conn, params=(symbol, start_date))
    else:
        df = pd.read_sql_query("""
            SELECT trade_date as date, symbol, open, high, low, close, volume
            FROM stock_prices WHERE symbol = ? ORDER BY trade_date ASC
        """, conn, params=(symbol,))
    conn.close()
    if df.empty:
        return df
    df['date'] = pd.to_datetime(df['date'])
    return df


def backtest_stock(symbol, sector, start_date=None):
    df = load_stock_data(symbol, start_date)
    if df.empty or len(df) < 60:
        return []
    df = df.reset_index(drop=True)  # 确保 0-based index
    df = calculate_all_indicators(df)
    bullish_index, bearish_index, df = build_pattern_index(df)
    trades = []
    for idx in range(30, len(df)):
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
            if idx + FORWARD_DAYS >= len(df):
                continue
            entry = df['close'].iloc[idx]
            exit_p = df['close'].iloc[idx + FORWARD_DAYS]
            ret = (exit_p - entry) / entry
            if direction == 'bearish':
                ret = -ret
            trades.append({
                'symbol': symbol, 'sector': sector,
                'indicator': ind_sig.indicator, 'signal': ind_sig.name,
                'direction': direction,
                'matched_pattern': matched_pattern or 'None',
                'has_pattern': has_pattern,
                'volume_confirmed': vol_confirmed,
                'return': ret,
                'is_success': ret > THRESHOLD,
                'trade_date': df['date'].iloc[idx],
            })
    return trades


def run_backtest_for_period(name, start_date=None):
    print(f"\n{'='*60}")
    print(f"📅 回測區間: {name}")
    print(f"{'='*60}")
    sector_map = fetch_sp500_sectors()
    sector_map = dict(zip(sector_map['ticker'], sector_map['sector']))
    tickers = load_constituents('sp500')
    all_trades = []
    for i, sym in enumerate(tickers):
        sector = sector_map.get(sym, 'Unknown')
        trades = backtest_stock(sym, sector, start_date)
        all_trades.extend(trades)
        if (i + 1) % 50 == 0:
            print(f"  ... 已處理 {i+1}/{len(tickers)} 檔")
    print(f"  ✅ 總信號: {len(all_trades)}")
    if not all_trades:
        return None, None
    stock_agg = defaultdict(lambda: {'count': 0, 'successes': 0, 'total_return': 0.0})
    sector_agg = defaultdict(lambda: {'count': 0, 'successes': 0, 'total_return': 0.0})
    for t in all_trades:
        sk = (t['symbol'], t['sector'], t['signal'], t['matched_pattern'], t['has_pattern'], t['volume_confirmed'])
        sk2 = (t['sector'], t['signal'], t['matched_pattern'], t['has_pattern'], t['volume_confirmed'])
        for agg, key in [(stock_agg, sk), (sector_agg, sk2)]:
            agg[key]['count'] += 1
            agg[key]['total_return'] += t['return']
            if t['is_success']:
                agg[key]['successes'] += 1

    stock_df = pd.DataFrame([
        {**{'symbol': k[0], 'sector': k[1], 'signal': k[2], 'matched_pattern': k[3], 'has_pattern': k[4], 'volume_confirmed': k[5],
            'count': v['count'], 'win_rate': v['successes']/v['count'], 'avg_return': v['total_return']/v['count']}}
        for k, v in stock_agg.items()
        if v['count'] >= MIN_SIGNALS
    ])
    sector_df = pd.DataFrame([
        {**{'sector': k[0], 'signal': k[1], 'matched_pattern': k[2], 'has_pattern': k[3], 'volume_confirmed': k[4],
            'count': v['count'], 'win_rate': v['successes']/v['count'], 'avg_return': v['total_return']/v['count']}}
        for k, v in sector_agg.items()
        if v['count'] >= MIN_SIGNALS
    ])
    return stock_df, sector_df


def lookup_trmb(name, stock_df, sector_df):
    sig = 'KDJ 超賣區金叉'
    pat = 'Support'
    vol = False
    has_pat = True
    # Stock level
    stk = stock_df[
        (stock_df['symbol'] == 'TRMB') &
        (stock_df['signal'] == sig) &
        (stock_df['matched_pattern'] == pat) &
        (stock_df['has_pattern'] == has_pat) &
        (stock_df['volume_confirmed'] == vol)
    ]
    # Sector level
    sec = sector_df[
        (sector_df['sector'] == 'Information Technology') &
        (sector_df['signal'] == sig) &
        (sector_df['matched_pattern'] == pat) &
        (sector_df['has_pattern'] == has_pat) &
        (sector_df['volume_confirmed'] == vol)
    ]
    # Fallback (all sectors)
    fb_sec = sector_df[
        (sector_df['signal'] == sig) &
        (sector_df['matched_pattern'] == pat) &
        (sector_df['has_pattern'] == has_pat) &
        (sector_df['volume_confirmed'] == vol)
    ]
    print(f"\n{name}: TRMB | KDJ 超賣區金叉 | Support | vol={vol} | has_pat={has_pat}")
    if not stk.empty:
        r = stk.iloc[0]
        print(f"  Stock level: win={r['win_rate']:.1%} avg={r['avg_return']:+.2f}% n={r['count']}")
    else:
        print(f"  Stock level: 無數據 (n<{MIN_SIGNALS})")
    if not sec.empty:
        r = sec.iloc[0]
        print(f"  Sector level (IT): win={r['win_rate']:.1%} avg={r['avg_return']:+.2f}% n={r['count']}")
    else:
        print(f"  Sector level (IT): 無數據 (n<{MIN_SIGNALS})")
    if not fb_sec.empty:
        r = fb_sec.iloc[0]
        print(f"  Fallback (all sectors): win={r['win_rate']:.1%} avg={r['avg_return']:+.2f}% n={r['count']}")
    else:
        print(f"  Fallback (all sectors): 無數據 (n<{MIN_SIGNALS})")


if __name__ == '__main__':
    # 全量數據（2021-10-07 起）
    stk_full, sec_full = run_backtest_for_period("全量數據 (2021-10 起)")
    # 1年數據（2025-05-26 起）
    stk_1yr, sec_1yr = run_backtest_for_period("1年數據 (2025-05-26 起)", start_date='2025-05-26')
    print("\n" + "="*60)
    print("🔍 TRMB 勝率對比：KDJ 超賣區金叉 + Support")
    print("="*60)
    lookup_trmb("【全量】", stk_full, sec_full)
    lookup_trmb("【1年】", stk_1yr, sec_1yr)