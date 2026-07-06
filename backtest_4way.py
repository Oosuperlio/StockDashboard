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
    detect_ema_signals, detect_bb_signals,
    detect_price_breakout_signals, detect_volume_signals,
    detect_adx_signals, detect_momentum_summary_signals
)
from pattern_detector import (
    Pattern, detect_doji, detect_hammer, detect_shooting_star,
    detect_morning_star, detect_evening_star, detect_engulfing,
    detect_harami, detect_support_resistance, detect_flag,
    detect_triangle,
    detect_cup_handle, detect_pullback_ema_support,
    detect_consolidation_breakout,
)

# ── 參數 ──────────────────────────────────────────────────────────────
FORWARD_DAYS = 5
THRESHOLD = 0.02
MIN_SIGNALS = 5
MIN_PATTERN_CONFIDENCE = 0.5
VOL_MA_PERIOD = 20
VOL_SPIKE_TODAY = 1.5
VOL_SPIKE_NEXT = 1.2

# 3年滾動回測窗口（從今天往回推 3年）
BACKTEST_START = '2023-06-23'   # 回測窗口起始（之前數據用於指標計算）
BACKTEST_END   = '2026-06-23'   # 回測窗口結束
WARMUP_DAYS    = 90             # 窗口前預留天數（用於均線/指標計算）

BULLISH_INDICATORS = {
    # ── 超賣反轉（現有） ──
    ('RSI', 'RSI 超賣區域 (30)'),
    ('RSI', 'RSI 維持超賣'),
    ('BB', 'BB 跌破下軌 (超賣)'),
    ('MACD', 'MACD 金叉 (空頭區)'),
    ('MACD', 'MACD 突破 0 軸'),
    ('KDJ', 'KDJ 超賣區金叉'),
    ('EMA', 'EMA 黃金交叉 (20 上穿 50)'),
    ('EMA', '價格突破 EMA20'),
    # ── 動能延續（新增，需與 signal_scanner.py 的 CORE_BULLISH_INDICATORS 一致）──
    ('MACD', 'MACD 金叉 (多頭區)'),
    ('EMA', 'EMA 多頭排列 (20>50>200)'),
    ('KDJ', 'KDJ 金叉'),
    ('RSI', 'RSI 上穿 50 中性線'),
    ('RSI', 'RSI 維持強勢 (50-70)'),
    ('RSI', 'RSI 加速上升'),
    ('RSI', 'RSI 動能加速（強勢區）'),
    ('BB', '價格在 BB 中軌上方'),
    ('BB', 'BB 中軌向上（上升趨勢）'),
    ('PRICE', '價格創 20 日新高'),
    ('PRICE', '價格創 60 日新高'),
    ('VOLUME', '成交量配合上升（放量上漲）'),
    ('VOLUME', '縮量回調（買點信號）'),
    ('VOLUME', '放量突破（強勢確認）'),
    ('ADX', 'ADX 強趨勢（多頭主導）'),
    ('ADX', 'ADX 極強趨勢（多頭）'),
    ('MOMENTUM', '多頭排列 + 價格在均線上方（強勢確認）'),
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
    'Bull Flag', 'Hammer', 'Ascending Triangle', 'Bullish Harami',
    # 新增延續形態（需與 signal_scanner.py 的 CORE_BULLISH_PATTERNS 一致）
    'Cup & Handle', 'Pullback EMA20', 'Consolidation Breakout',
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
        with open(path, 'r') as f:
            return [line.strip() for line in f if line.strip()]
    elif market == 'us-extended':
        # 從 DB 讀取所有美股（覆蓋擴展宇宙 ~1,101 隻）
        try:
            conn = duckdb.connect(str(Path(__file__).parent / 'data' / 'prices.ddb'), read_only=True)
            rows = conn.execute(
                "SELECT DISTINCT symbol FROM stock_prices WHERE symbol NOT LIKE '%.HK' AND symbol NOT LIKE 'hk%' ORDER BY symbol"
            ).fetchall()
            conn.close()
            return [r[0] for r in rows]
        except Exception as e:
            print(f"  ⚠️ DB 讀取失敗: {e}，回退到 SP500")
            path = Path(__file__).parent / 'data' / 'constituents_sp500.txt'
            with open(path, 'r') as f:
                return [line.strip() for line in f if line.strip()]
    else:
        path = Path(__file__).parent / 'data' / 'constituents_hsi.txt'
        with open(path, 'r') as f:
            return [line.strip() for line in f if line.strip()]

def get_sector_map():
    # 使用統一 sector 快取（Yahoo Finance）
    _cache_path = Path(__file__).parent / 'data' / 'sector_cache.json'
    if _cache_path.exists():
        try:
            import json
            with open(_cache_path) as _f:
                _cache = json.load(_f)
            _sm = {}
            for _tk, _info in _cache.items():
                _sec = _info.get('sector', 'Unknown')
                if _sec != 'Unknown':
                    _sm[_tk] = _sec
            if _sm:
                print(f"  📂 從統一快取載入 {len(_sm)} 檔股票的 sector 映射")
                return _sm
        except Exception:
            pass
    # fallback 到 Wikipedia
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

    # ✨ 新增：延續形態檢測（滾動窗口，每根K線都檢測）
    # 這些形態只檢查 df 的最後 N 根 K 線，所以需要逐窗口調用
    # 優化：使用視圖而非複製，避免 reset_index 開銷
    for idx in range(40, len(df)):
        for detector in [detect_cup_handle, detect_pullback_ema_support, detect_consolidation_breakout]:
            try:
                # 只傳入到當前 idx 為止的切片（視圖，不複製）
                slice_df = df.iloc[:idx+1]
                patterns = detector(slice_df)
                for p in patterns:
                    if p.confidence < MIN_PATTERN_CONFIDENCE:
                        continue
                    pi = PatternIndex(p)
                    # 切片中的最後一根K線 = idx
                    if p.direction == 'bullish' and p.name in BULLISH_PATTERNS:
                        bullish_index[idx].append(pi)
                    elif p.direction == 'bearish' and p.name in BEARISH_PATTERNS:
                        bearish_index[idx].append(pi)
            except Exception:
                pass

    return bullish_index, bearish_index, df


# ── 數據加載 ──────────────────────────────────────────────────────────

def load_stock_data(symbol):
    db_path = Path(__file__).parent / 'data' / 'prices.ddb'
    conn = duckdb.connect(str(db_path), read_only=True)
    # 加載 WARMUP_DAYS 前的數據，確保指標計算有足夠的歷史
    data_start = pd.Timestamp(BACKTEST_START) - pd.Timedelta(days=WARMUP_DAYS)
    df = pd.read_sql_query("""
        SELECT trade_date as date, symbol, open, high, low, close, volume
        FROM stock_prices
        WHERE symbol = ? AND trade_date >= ? AND trade_date <= ?
        ORDER BY trade_date ASC
    """, conn, params=(symbol, data_start.date(), pd.Timestamp(BACKTEST_END).date()))
    conn.close()
    if df.empty:
        return df
    df['date'] = pd.to_datetime(df['date'])
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
        # 只回測 BACKTEST_START → BACKTEST_END 區間內的信號
        signal_date = df['date'].iloc[idx]
        cutoff_start = pd.Timestamp(BACKTEST_START)
        cutoff_end = pd.Timestamp(BACKTEST_END)
        if signal_date < cutoff_start or signal_date > cutoff_end:
            continue

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
        all_ind_signals.extend(detect_price_breakout_signals(df, idx))
        all_ind_signals.extend(detect_volume_signals(df, idx))
        all_ind_signals.extend(detect_adx_signals(df, idx))
        all_ind_signals.extend(detect_momentum_summary_signals(df, idx))

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
                'matched_pattern': matched_pattern or 'None',
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
    print(f"📅 回測窗口：{BACKTEST_START} → {BACKTEST_END}（3年滾動）")
    print(f"📦 預熱天數：{WARMUP_DAYS}天（用於指標計算）")
    print("=" * 80)

    sector_map = get_sector_map()
    print(f"\n已載入 {len(sector_map)} 檔股票的 sector 映射")

    all_trades = []

    for market in ['sp500', 'us-extended', 'hsi']:
        tickers = load_constituents(market)
        market_name = '美股擴展宇宙' if market == 'us-extended' else ('S&P 500' if market == 'sp500' else 'HSI')
        print(f"\n📂 回測 {market_name} ({len(tickers)} 檔)...")
        if market == 'us-extended':
            print(f"   (新增 {len(tickers)} 隻全量 US 股票 — 含擴展宇宙)")
        if not tickers:
            print(f"  ⏭️  {market_name} 無股票，跳過")

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

    # ── 雙層聚合 ───────────────────────────────────────────────────────────
    # 層次1：個股級（每隻股票單獨計算）
    stock_agg = defaultdict(lambda: {'count': 0, 'successes': 0, 'total_return': 0.0})
    # 層次2：Sector 級（同行業所有股票合併）
    sector_agg_2 = defaultdict(lambda: {'count': 0, 'successes': 0, 'total_return': 0.0})

    for t in all_trades:
        sk = (t['symbol'], t['sector'], t['signal'],
              t['matched_pattern'], t['has_pattern'], t['volume_confirmed'])
        sk2 = (t['sector'], t['signal'],
               t['matched_pattern'], t['has_pattern'], t['volume_confirmed'])
        for agg, key in [(stock_agg, sk), (sector_agg_2, sk2)]:
            agg[key]['count'] += 1
            agg[key]['total_return'] += t['return']
            if t['is_success']:
                agg[key]['successes'] += 1

    # ── 建立個股級 DataFrame ──────────────────────────────────────────────
    stock_rows = []
    for (sym, sector, signal, matched_pattern, has_pattern, vol_confirmed), stats in stock_agg.items():
        if stats['count'] < MIN_SIGNALS:
            continue
        wr = stats['successes'] / stats['count']
        avg_ret = stats['total_return'] / stats['count']
        stock_rows.append({
            'symbol': sym,
            'sector': sector,
            'signal': signal,
            'matched_pattern': matched_pattern,
            'has_pattern': has_pattern,
            'volume_confirmed': vol_confirmed,
            'count': stats['count'],
            'win_rate': wr,
            'avg_return': avg_ret,
        })

    stock_df = pd.DataFrame(stock_rows)
    if not stock_df.empty:
        # 建立 base 查找表（symbol × signal → win_rate），避免 O(n²) 循環查詢
        base_lookup = {}
        base_df = stock_df[
            (stock_df['matched_pattern'] == 'None') &
            (stock_df['has_pattern'] == False) &
            (stock_df['volume_confirmed'] == False)
        ]
        for _, br in base_df.iterrows():
            base_lookup[(br['symbol'], br['signal'])] = br['win_rate']

        stock_df['improvement'] = 0.0
        for idx, row in stock_df.iterrows():
            base_wr = base_lookup.get((row['symbol'], row['signal']))
            if base_wr is not None:
                stock_df.loc[idx, 'improvement'] = row['win_rate'] - base_wr
        stock_path = Path(__file__).parent / 'backtest_4way_results.csv'
        stock_df.to_csv(stock_path, index=False)
        print(f"💾 [個股級] 已保存：{stock_path}（{len(stock_df)} 組合）")
    else:
        print("⚠️ 個股級聚合後無足夠樣本")

    # ── 建立 Sector 級 DataFrame ───────────────────────────────────────────
    sector_rows2 = []
    for (sector, signal, matched_pattern, has_pattern, vol_confirmed), stats in sector_agg_2.items():
        if stats['count'] < MIN_SIGNALS:
            continue
        wr = stats['successes'] / stats['count']
        avg_ret = stats['total_return'] / stats['count']
        sector_rows2.append({
            'sector': sector,
            'signal': signal,
            'matched_pattern': matched_pattern,
            'has_pattern': has_pattern,
            'volume_confirmed': vol_confirmed,
            'count': stats['count'],
            'win_rate': wr,
            'avg_return': avg_ret,
        })

    sector_df2 = pd.DataFrame(sector_rows2)
    if not sector_df2.empty:
        # 建立 Sector 級 base 查找表（sector × signal → win_rate）
        s_base_lookup = {}
        s_base_df = sector_df2[
            (sector_df2['matched_pattern'] == 'None') &
            (sector_df2['has_pattern'] == False) &
            (sector_df2['volume_confirmed'] == False)
        ]
        for _, br in s_base_df.iterrows():
            s_base_lookup[(br['sector'], br['signal'])] = br['win_rate']

        sector_df2['improvement'] = 0.0
        for idx, row in sector_df2.iterrows():
            base_wr = s_base_lookup.get((row['sector'], row['signal']))
            if base_wr is not None:
                sector_df2.loc[idx, 'improvement'] = row['win_rate'] - base_wr
        sector_path2 = Path(__file__).parent / 'backtest_sector_results.csv'
        sector_df2.to_csv(sector_path2, index=False)
        print(f"💾 [Sector 級] 已保存：{sector_path2}（{len(sector_df2)} 組合）")
    else:
        print("⚠️ Sector 級聚合後無足夠樣本")

    # ── 打印：個股級高勝率 TOP10 ───────────────────────────────────────────
    print("\n" + "=" * 100)
    print("🏆 個股級高勝率（形態 + 成交量確認，勝率 ≥ 60%，n ≥ 10）")
    print("=" * 100)
    top_s = stock_df[
        (stock_df['has_pattern'] == True) &
        (stock_df['volume_confirmed'] == True) &
        (stock_df['win_rate'] >= 0.60) &
        (stock_df['count'] >= 10)
    ].sort_values('win_rate', ascending=False).head(10)

    print(f"\n{'Symbol':<8} {'Sector':<18} {'Signal':<28} {'n':>5} {'勝率':>8} {'回報':>9} {'提升':>8}")
    print("-" * 100)
    for _, r in top_s.iterrows():
        print(f"{r['symbol']:<8} {r['sector']:<18} {r['signal']:<28} {r['count']:>5} "
              f"{r['win_rate']:>8.1%} {r['avg_return']:>+9.2%} {r['improvement']:>+8.1%}")

    # ── 打印：Sector 級高勝率 TOP10 ─────────────────────────────────────────
    print("\n" + "=" * 100)
    print("📊 Sector 級高勝率（形態 + 成交量確認，勝率 ≥ 60%，n ≥ 10）")
    print("=" * 100)
    top_sec = sector_df2[
        (sector_df2['has_pattern'] == True) &
        (sector_df2['volume_confirmed'] == True) &
        (sector_df2['win_rate'] >= 0.60) &
        (sector_df2['count'] >= 10)
    ].sort_values('win_rate', ascending=False).head(10)

    print(f"\n{'Sector':<22} {'Signal':<28} {'n':>5} {'勝率':>8} {'回報':>9} {'提升':>8}")
    print("-" * 100)
    for _, r in top_sec.iterrows():
        print(f"{r['sector']:<22} {r['signal']:<28} {r['count']:>5} "
              f"{r['win_rate']:>8.1%} {r['avg_return']:>+9.2%} {r['improvement']:>+8.1%}")

    # ── 打印：成交量確認提升（個股級）───────────────────────────────────────
    print("\n" + "=" * 100)
    print("📈 成交量確認勝率提升 TOP10（個股級，has_pattern=True）")
    print("=" * 100)
    vol_s = stock_df[
        (stock_df['has_pattern'] == True) & (stock_df['count'] >= 10)
    ].copy()
    vol_s['vol_lift'] = 0.0
    for idx, row in vol_s.iterrows():
        no_vol = vol_s[
            (vol_s['symbol'] == row['symbol']) &
            (vol_s['signal'] == row['signal']) &
            (vol_s['has_pattern'] == row['has_pattern']) &
            (vol_s['volume_confirmed'] == False)
        ]
        if not no_vol.empty:
            vol_s.loc[idx, 'vol_lift'] = row['win_rate'] - no_vol.iloc[0]['win_rate']
    top_lift_s = vol_s[vol_s['volume_confirmed'] == True].nlargest(10, 'vol_lift')
    print(f"\n{'Symbol':<8} {'Signal':<28} {'n':>5} {'有量':>8} {'無量':>8} {'提升':>8}")
    print("-" * 100)
    for _, r in top_lift_s.iterrows():
        no_vol = vol_s[
            (vol_s['symbol'] == r['symbol']) &
            (vol_s['signal'] == r['signal']) &
            (vol_s['has_pattern'] == r['has_pattern']) &
            (vol_s['volume_confirmed'] == False)
        ]
        no_vol_wr = no_vol.iloc[0]['win_rate'] if not no_vol.empty else 0
        print(f"{r['symbol']:<8} {r['signal']:<28} {r['count']:>5} "
              f"{r['win_rate']:>8.1%} {no_vol_wr:>8.1%} {r['vol_lift']:>+8.1%}")

    print("\n✅ 四維回測完成！")


if __name__ == '__main__':
    main()
