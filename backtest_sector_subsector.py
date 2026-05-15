#!/usr/bin/env python3
"""
backtest_sector_subsector.py — 指標 + 形態 + Sector/SubSector 三維回測引擎
================================================================================
目標：將 Sector / SubSector 作為額外因子加入回測，分析行業分化對勝率的影響，
      並對比「有 / 無 Sector 因子」時的準確率差異。

輸出：
  • backtest_sector_subsector_results.csv   — 完整三維回測結果
  • backtest_sector_improvement.csv          — Sector 因子提升幅度排行
"""

import sys
import os
import argparse
import warnings
warnings.filterwarnings('ignore')

from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict

import pandas as pd
import numpy as np
import requests
from io import StringIO

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import duckdb

from indicator_calculator import calculate_all_indicators
from indicator_signals import (
    IndicatorSignal,
    detect_rsi_signals, detect_macd_signals, detect_kdj_signals,
    detect_ema_signals, detect_bb_signals
)
from pattern_detector import (
    Pattern, detect_doji, detect_hammer, detect_shooting_star,
    detect_morning_star, detect_evening_star, detect_engulfing,
    detect_harami, detect_support_resistance, detect_flag,
    detect_triangle
)

# ─── 參數 ───────────────────────────────────────────────────────────────────
FORWARD_DAYS = 5
THRESHOLD = 0.02
MIN_SIGNALS = 8          # 最小樣本數（可降低至 5 獲得更多細分數據）
MIN_PATTERN_CONFIDENCE = 0.5

# ─── 行業分類 ───────────────────────────────────────────────────────────────

def fetch_sp500_sectors() -> pd.DataFrame:
    """從 Wikipedia 抓取 S&P 500 GICS Sector / Sub-Industry"""
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
    resp = requests.get(url, headers=headers, timeout=15)
    tables = pd.read_html(StringIO(resp.text))
    df = tables[0][['Symbol', 'GICS Sector', 'GICS Sub-Industry']].rename(
        columns={'Symbol': 'ticker', 'GICS Sector': 'sector', 'GICS Sub-Industry': 'subsector'})
    df['ticker'] = df['ticker'].str.strip()
    return df


def fetch_hsi_sectors() -> pd.DataFrame:
    """從 Wikipedia 抓取 HSI Sector 分類"""
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    url = 'https://en.wikipedia.org/wiki/Hang_Seng_Index'
    resp = requests.get(url, headers=headers, timeout=15)
    tables = pd.read_html(StringIO(resp.text))

    # HSI constituents table: 找到有 'Ticker' 或 'ticker' 欄位的 table
    for t in tables:
        cols = [str(c) for c in t.columns.tolist()]
        if any('Ticker' in c or 'Sub-index' in c for c in cols):
            df = t.copy()
            df.columns = [str(c) for c in df.columns]
            # 找到 Ticker 和 Sub-index 欄位
            ticker_col = [c for c in df.columns if 'Ticker' in c][0]
            sector_col = [c for c in df.columns if 'Sub-index' in c][0]

            def convert_hk(tk):
                tk = str(tk).replace('SEHK:\xa0', '').replace('SEHK:', '').strip()
                return tk.zfill(4) + '.HK'
            df['ticker'] = df[ticker_col].apply(convert_hk)
            df['sector'] = df[sector_col]
            df['subsector'] = df['sector']   # HSI 無 sub-sector，用 sector 代替
            return df[['ticker', 'sector', 'subsector']]
    return pd.DataFrame(columns=['ticker', 'sector', 'subsector'])


# ─── 形態索引 ────────────────────────────────────────────────────────────────

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


@dataclass
class PatternIndex:
    pattern: Pattern
    def covers(self, idx: int) -> bool:
        return idx in self.pattern.indices


def build_pattern_index(df: pd.DataFrame) -> Tuple[dict, dict]:
    bullish_index: dict = defaultdict(list)
    bearish_index: dict = defaultdict(list)

    for idx in range(5, len(df)):
        for detector in [
            lambda i: detect_doji(df, i),
            lambda i: detect_hammer(df, i),
            lambda i: detect_shooting_star(df, i),
            lambda i: detect_morning_star(df, i),
            lambda i: detect_evening_star(df, i),
            lambda i: detect_engulfing(df, i),
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

    return bullish_index, bearish_index


# ─── 數據加載 ────────────────────────────────────────────────────────────────

def load_stock_data(symbol: str) -> pd.DataFrame:
    db_path = Path(__file__).parent / 'data' / 'prices.ddb'
    conn = duckdb.connect(str(db_path), read_only=True)
    df = pd.read_sql("""
        SELECT trade_date as date, symbol, open, high, low, close, volume
        FROM stock_prices WHERE symbol = ?
        ORDER BY trade_date ASC
    """, conn, params=(symbol,))
    conn.close()
    if df.empty:
        return df
    df['date'] = pd.to_datetime(df['date'])
    df.set_index('date', inplace=True)
    return df


def load_constituents(market: str) -> list:
    if market == 'sp500':
        path = Path(__file__).parent / 'data' / 'constituents_sp500.txt'
    else:
        path = Path(__file__).parent / 'data' / 'constituents_hsi.txt'
    with open(path, 'r') as f:
        return [line.strip() for line in f if line.strip()]


# ─── 複合信號檢測 ────────────────────────────────────────────────────────────

@dataclass
class CombinedSignal:
    indicator: str
    indicator_signal: str
    indicator_direction: str
    pattern: str
    pattern_confidence: float
    confidence: float


def detect_combined_signals_at_idx(
    df: pd.DataFrame, idx: int,
    bullish_index: dict, bearish_index: dict
) -> List[CombinedSignal]:
    signals = []
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
        matched_confidence = 0.0

        if direction == 'bullish':
            for pi in bull_pis:
                if pi.pattern.confidence > matched_confidence:
                    matched_pattern = pi.pattern.name
                    matched_confidence = pi.pattern.confidence
        else:
            for pi in bear_pis:
                if pi.pattern.confidence > matched_confidence:
                    matched_pattern = pi.pattern.name
                    matched_confidence = pi.pattern.confidence

        if matched_pattern:
            conf = min(1.0, ind_sig.confidence * 1.2 + matched_confidence * 0.3)
            signals.append(CombinedSignal(
                indicator=ind_sig.indicator,
                indicator_signal=ind_sig.name,
                indicator_direction=direction,
                pattern=matched_pattern,
                pattern_confidence=matched_confidence,
                confidence=conf,
            ))
        else:
            signals.append(CombinedSignal(
                indicator=ind_sig.indicator,
                indicator_signal=ind_sig.name,
                indicator_direction=direction,
                pattern='None',
                pattern_confidence=0.0,
                confidence=ind_sig.confidence * 0.9,
            ))
    return signals


# ─── 回測核心 ────────────────────────────────────────────────────────────────

def backtest_signal(df: pd.DataFrame, signal_date_idx: int,
                    direction: str) -> Optional[dict]:
    if signal_date_idx + FORWARD_DAYS >= len(df):
        return None
    entry_price = df['close'].iloc[signal_date_idx]
    exit_price = df['close'].iloc[signal_date_idx + FORWARD_DAYS]
    ret = (exit_price - entry_price) / entry_price
    if direction == 'bearish':
        ret = -ret
    return {'return': ret, 'is_success': ret > THRESHOLD}


def backtest_stock(symbol: str, sector: str, subsector: str) -> Tuple[List[dict], List[dict]]:
    """
    回測單一股票，返回 (含Sector標籤的結果, 純指標結果)
    """
    df = load_stock_data(symbol)
    if df.empty or len(df) < 60:
        return [], []

    try:
        df = calculate_all_indicators(df)
        bullish_index, bearish_index = build_pattern_index(df)
    except Exception:
        return [], []

    combined_results = []
    indicator_only_results = []

    for idx in range(30, len(df)):
        signals = detect_combined_signals_at_idx(df, idx, bullish_index, bearish_index)
        for sig in signals:
            result = backtest_signal(df, idx, sig.indicator_direction)
            if result is None:
                continue

            base = {
                'symbol': symbol,
                'sector': sector,
                'subsector': subsector,
                'date': df.index[idx],
                'indicator': sig.indicator,
                'signal': sig.indicator_signal,
                'direction': sig.indicator_direction,
                'confidence': sig.confidence,
                'pattern': sig.pattern,
                'pattern_confidence': sig.pattern_confidence,
                **result
            }

            if sig.pattern != 'None':
                combined_results.append(base)
            else:
                indicator_only_results.append(base)

    return combined_results, indicator_only_results


# ─── 聚合分析 ────────────────────────────────────────────────────────────────

def aggregate_all(
    combined: List[dict],
    indicator_only: List[dict],
    min_count: int = MIN_SIGNALS
) -> pd.DataFrame:
    """
    三維聚合：(sector, subsector, indicator, signal, pattern, direction)
    同時計算復合策略與純指標策略的勝率。
    """
    all_records = []
    for r in combined + indicator_only:
        all_records.append({**r, 'has_pattern': r['pattern'] != 'None'})

    df = pd.DataFrame(all_records)
    if df.empty:
        return df

    # 聚合
    grouped = df.groupby(['sector', 'subsector', 'indicator', 'signal',
                            'direction', 'has_pattern']).agg(
        count=('return', 'count'),
        successes=('is_success', 'sum'),
        avg_return=('return', 'mean'),
    ).reset_index()

    grouped['win_rate'] = grouped['successes'] / grouped['count']
    grouped['strategy'] = grouped['has_pattern'].map({True: '✅ 複合', False: '⚪ 純指標'})
    grouped = grouped[grouped['count'] >= min_count]

    # 計算復合相對於純指標的勝率提升（在同一 sector + signal + direction 內）
    grouped['improvement'] = 0.0
    for idx, row in grouped[grouped['has_pattern'] == True].iterrows():
        base = grouped[
            (grouped['signal'] == row['signal']) &
            (grouped['sector'] == row['sector']) &
            (grouped['has_pattern'] == False) &
            (grouped['direction'] == row['direction'])
        ]
        if not base.empty:
            grouped.loc[idx, 'improvement'] = row['win_rate'] - base.iloc[0]['win_rate']

    return grouped.reset_index(drop=True)


def aggregate_sector_improvement(combined_records: List[dict],
                                   indicator_records: List[dict]) -> pd.DataFrame:
    """
    計算每個 (sector, signal) 的復合 vs 純指標勝率提升，
    用於回答：「加入 Sector 因子後，勝率提升多少？」
    """
    # 將記錄轉為 DataFrame 並計算勝率
    comb_df = aggregate_all(combined_records, indicator_records)
    if comb_df.empty:
        return pd.DataFrame()

    # 只看 bullish 買入信號
    bull = comb_df[comb_df['direction'] == 'bullish'].copy()
    sector_rows = []

    # 1. Sector × 信號 維度
    for sector in bull['sector'].unique():
        sec_bull = bull[bull['sector'] == sector]
        for signal in sec_bull['signal'].unique():
            c = sec_bull[(sec_bull['signal'] == signal) & (sec_bull['has_pattern'] == True)]
            i = sec_bull[(sec_bull['signal'] == signal) & (sec_bull['has_pattern'] == False)]
            if len(c) < MIN_SIGNALS:
                continue
            c_wr = c['win_rate'].mean()
            c_avg = c['avg_return'].mean()
            c_count = c['count'].sum()
            i_wr = i['win_rate'].mean() if len(i) >= MIN_SIGNALS else np.nan
            i_avg = i['avg_return'].mean() if len(i) >= MIN_SIGNALS else np.nan
            sector_rows.append({
                'sector': sector,
                'signal': signal,
                'count': c_count,
                'combo_win_rate': c_wr,
                'ind_win_rate': i_wr,
                'improvement': c_wr - i_wr if not np.isnan(i_wr) else np.nan,
                'combo_avg_return': c_avg,
                'ind_avg_return': i_avg,
                'level': 'sector',
            })

    # 2. SubSector × 信號 維度
    for subsec in bull['subsector'].unique():
        if not subsec or subsec == '' or subsec == 'Unknown':
            continue
        sub_bull = bull[bull['subsector'] == subsec]
        for signal in sub_bull['signal'].unique():
            c = sub_bull[(sub_bull['signal'] == signal) & (sub_bull['has_pattern'] == True)]
            if len(c) < 5:
                continue
            c_wr = c['win_rate'].mean()
            c_avg = c['avg_return'].mean()
            c_count = c['count'].sum()
            sector_rows.append({
                'sector': subsec,
                'signal': signal,
                'count': c_count,
                'combo_win_rate': c_wr,
                'ind_win_rate': np.nan,
                'improvement': np.nan,
                'combo_avg_return': c_avg,
                'ind_avg_return': np.nan,
                'level': 'subsector',
            })

    if not sector_rows:
        return pd.DataFrame()
    return pd.DataFrame(sector_rows).sort_values(['level', 'improvement'], ascending=[True, False])


def print_top_improvements(df: pd.DataFrame, market: str, top_n: int = 20):
    print(f"\n{'='*100}")
    print(f"📊 {market} — Sector/SubSector 因子對勝率的影響（Top {top_n} 提升幅度）")
    print(f"{'='*100}")
    if df is None or df.empty:
        print("  無足夠數據")
        return

    # df 已經是 bullish 信號的數據（由 aggregate_sector_improvement 過濾）
    print(f"\n{'─'*100}")
    print(f"  {'行業/版塊':<35} {'信號':<28} {'復合勝率':>9} {'純指標勝率':>10} {'勝率提升':>10} {'樣本':>6}")
    print(f"{'─'*100}")

    # Sector × Signal 維度，按提升排序
    sec = df[df['level'] == 'sector'].sort_values('improvement', ascending=False).head(top_n)
    for _, r in sec.iterrows():
        ind_wr = r['ind_win_rate']
        ind_str = f"{ind_wr:.1%}" if not np.isnan(ind_wr) else "N/A"
        imp = r['improvement']
        imp_str = f"{imp:+.1%}" if not np.isnan(imp) else "N/A"
        print(f"  {r['sector'][:35]:<35} {r['signal'][:28]:<28} "
              f"{r['combo_win_rate']:>8.1%} {ind_str:>10} {imp_str:>10} {r['count']:>6.0f}")

    print(f"\n{'─'*100}")
    print(f"  📌 SubSector × 信號（勝率最佳）:")
    sub = df[df['level'] == 'subsector'].sort_values('combo_win_rate', ascending=False).head(15)
    for _, r in sub.iterrows():
        print(f"  {r['sector'][:35]:<35} {r['signal'][:28]:<28} "
              f"{r['combo_win_rate']:>8.1%} {r['count']:>6.0f} → avg +{r['combo_avg_return']:.2%}")


# ─── 主程序 ─────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description='Sector × SubSector × 指標 × 形態 三維回測')
    parser.add_argument('--sp500', action='store_true')
    parser.add_argument('--hsi', action='store_true')
    parser.add_argument('--output', type=str, default='backtest_sector_subsector_results.csv')
    args = parser.parse_args()

    markets = []
    if args.sp500:
        markets.append(('sp500', 'S&P 500'))
    elif args.hsi:
        markets.append(('hsi', 'HSI'))
    else:
        markets = [('sp500', 'S&P 500'), ('hsi', 'HSI')]

    # 每個市場分開儲存，最後再做跨市場分析
    market_combined: Dict[str, List[dict]] = {}
    market_indicator: Dict[str, List[dict]] = {}

    for market, market_name in markets:
        print(f"\n\n{'#'*80}")
        print(f"# {market_name} 數據載入中...")
        print(f"{'#'*80}")

        # 載入行業分類
        if market == 'sp500':
            sector_df = fetch_sp500_sectors()
            print(f"  S&P 500 行業分類：{sector_df['sector'].nunique()} 個 Sector, "
                  f"{sector_df['subsector'].nunique()} 個 SubSector")
        else:
            sector_df = fetch_hsi_sectors()
            print(f"  HSI 行業分類：{sector_df['sector'].nunique()} 個 Sector")

        # 建立 ticker → sector 映射
        ticker_sector = {}
        ticker_subsector = {}
        for _, row in sector_df.iterrows():
            ticker_sector[row['ticker']] = row['sector']
            ticker_subsector[row['ticker']] = row.get('subsector', row['sector'])

        tickers = load_constituents(market)
        print(f"  成分股：{len(tickers)} 隻")

        # 回測
        processed = skipped = 0
        m_combined: List[dict] = []
        m_indicator: List[dict] = []

        for i, sym in enumerate(tickers):
            sector = ticker_sector.get(sym, 'Unknown')
            subsector = ticker_subsector.get(sym, sector)
            comb, ind = backtest_stock(sym, sector, subsector)
            if not comb and not ind:
                skipped += 1
            else:
                processed += 1
                m_combined.extend(comb)
                m_indicator.extend(ind)

            if (i + 1) % 50 == 0:
                print(f"  ... 已處理 {i+1} 隻股票，復合信號 {len(m_combined)} 個")

        print(f"\n  完成：處理 {processed} 隻，跳過 {skipped} 隻")
        print(f"  復合信號：{len(m_combined)} 個 | 純指標信號：{len(m_indicator)} 個")

        # 市場維度分析
        if m_combined:
            imp_df = aggregate_sector_improvement(m_combined, m_indicator)
            print_top_improvements(imp_df.assign(direction='bullish'), market_name)

        market_combined[market] = m_combined
        market_indicator[market] = m_indicator

    # ─── 全域聚合（所有市場）────────────────────────────
    all_combined = [r for v in market_combined.values() for r in v]
    all_indicator = [r for v in market_indicator.values() for r in v]

    if all_combined:
        full_df = aggregate_all(all_combined, all_indicator)
        out_path = Path(__file__).parent / args.output
        full_df.to_csv(out_path, index=False)
        print(f"\n\n💾 完整結果已儲存：{out_path}")
        print(f"   共 {len(full_df)} 行 × {len(full_df.columns)} 列")

        imp_df = aggregate_sector_improvement(all_combined, all_indicator)
        if not imp_df.empty:
            imp_path = Path(__file__).parent / 'backtest_sector_improvement.csv'
            imp_df.to_csv(imp_path, index=False)
            print(f"💾 提升幅度排行已儲存：{imp_path}")

if __name__ == '__main__':
    main()
