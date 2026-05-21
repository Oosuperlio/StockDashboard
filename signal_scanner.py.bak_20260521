#!/usr/bin/env python3
"""
signal_scanner.py — 每日技術指標 + 形態 + Sector 信號掃描引擎
================================================================
功能：
  1. 對全市場股票進行即時信號檢測
  2. 按 Sector × Signal × Pattern 三維勝率加權排序
  3. 輸出「今日值得關注的進場信號」列表
  4. 為 Dashboard / Telegram Cron 提供數據接口

使用方法：
  python signal_scanner.py                 # 掃描全市場
  python signal_scanner.py --ticker 0700.HK # 掃描單一股票
  python signal_scanner.py --tier 1         # 只輸出 Tier-1（最高置信度）
"""

import sys
import os
import argparse
import warnings
warnings.filterwarnings('ignore')

from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass
from typing import List, Optional, Tuple, Dict
from datetime import datetime

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

# ─── 參數 ───────────────────────────────────────────────────────────────────

MIN_PATTERN_CONFIDENCE = 0.5
LOOKBACK_DAYS = 60        # 用於形態計算的歷史K線數
MIN_SCANNER_SIGNALS = 8   # 最少樣本數（勝率數據門檻）

# 最佳 Sector × Signal × Pattern 組合（從 backtest_sector_subsector_results.csv 學習得來）
# 格式：(sector, signal, pattern) → 歷史勝率
BEST_COMBOS = {
    # (sector, signal, pattern) → (win_rate, avg_return, count)
    ('Financials', 'BB 跌破下軌 (超賣)', 'Support'): (0.75, 0.038, 45),
    ('Financials', 'BB 跌破下軌 (超賣)', 'Morning Star'): (0.70, 0.033, 38),
    ('Information Technology', 'BB 跌破下軌 (超賣)', 'Support'): (0.72, 0.036, 89),
    ('Information Technology', 'BB 跌破下軌 (超賣)', 'Bull Flag'): (0.68, 0.041, 52),
    ('Information Technology', 'RSI 超賣區域 (30)', 'Support'): (0.65, 0.031, 67),
    ('Energy', 'RSI 維持超賣', 'Support'): (0.64, 0.028, 42),
    ('Utilities', 'BB 跌破下軌 (超賣)', 'Support'): (0.76, 0.037, 41),
    ('Materials', 'RSI 超賣區域 (30)', 'Morning Star'): (0.62, 0.029, 35),
    ('Industrials', 'BB 跌破下軌 (超賣)', 'Support'): (0.58, 0.027, 78),
    ('Communication Services', 'BB 跌破下軌 (超賣)', 'Morning Star'): (0.60, 0.034, 60),
    ('Commerce & Industry', 'RSI 超賣區域 (30)', 'Support'): (0.60, 0.028, 68),
    ('Real Estate', 'RSI 維持超賣', 'Support'): (0.65, 0.030, 30),
    ('Properties', 'BB 跌破下軌 (超賣)', 'Support'): (0.51, 0.022, 81),
}

# 核心指標信號（入場意願高）
CORE_BULLISH_INDICATORS = {
    ('RSI', 'RSI 超賣區域 (30)'),
    ('RSI', 'RSI 維持超賣'),
    ('BB', 'BB 跌破下軌 (超賣)'),
    ('MACD', 'MACD 金叉 (空頭區)'),
    ('KDJ', 'KDJ 超賣區金叉'),
    ('EMA', '價格突破 EMA20'),
}

CORE_BULLISH_PATTERNS = {
    'Support', 'Morning Star', 'Bullish Engulfing', 'Bull Flag',
}

# ─── 行業分類 ────────────────────────────────────────────────────────────────

def fetch_sp500_sectors() -> pd.DataFrame:
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
    resp = requests.get(url, headers=headers, timeout=15)
    tables = pd.read_html(StringIO(resp.text))
    df = tables[0][['Symbol', 'GICS Sector', 'GICS Sub-Industry']].rename(
        columns={'Symbol': 'ticker', 'GICS Sector': 'sector',
                 'GICS Sub-Industry': 'subsector'})
    df['ticker'] = df['ticker'].str.strip()
    return df


def fetch_hsi_sectors() -> pd.DataFrame:
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
            df['subsector'] = df['sector']
            return df[['ticker', 'sector', 'subsector']]
    return pd.DataFrame(columns=['ticker', 'sector', 'subsector'])


# ─── 形態索引 ────────────────────────────────────────────────────────────────

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
                        if p.direction == 'bullish' and p.name in CORE_BULLISH_PATTERNS:
                            bullish_index[i].append(pi)
                        elif p.direction == 'bearish':
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
                    if p.direction == 'bullish' and p.name in CORE_BULLISH_PATTERNS:
                        bullish_index[i].append(pi)
                    elif p.direction == 'bearish':
                        bearish_index[i].append(pi)
        except Exception:
            pass

    return bullish_index, bearish_index


# ─── 數據加載 ───────────────────────────────────────────────────────────────

def load_latest_prices(symbols: List[str], days: int = 90) -> Dict[str, pd.DataFrame]:
    """從 DuckDB 載入最近 N 天的股價數據"""
    if not symbols:
        return {}

    db_path = Path(__file__).parent / 'data' / 'prices.ddb'
    conn = duckdb.connect(str(db_path), read_only=True)

    placeholders = ','.join(['?' for _ in symbols])
    df = pd.read_sql(f"""
        SELECT symbol, trade_date, open, high, low, close, volume
        FROM stock_prices
        WHERE symbol IN ({placeholders})
        ORDER BY symbol, trade_date DESC
    """, conn, params=symbols)
    conn.close()

    # 翻轉（由新到舊→由舊到新），每股票取最近 days 行
    result = {}
    for sym, grp in df.groupby('symbol'):
        grp = grp.sort_values('trade_date').tail(days).reset_index(drop=True)
        grp['trade_date'] = pd.to_datetime(grp['trade_date'])
        grp.set_index('trade_date', inplace=True)
        result[sym] = grp

    return result


def load_ticker_sector_map() -> Tuple[Dict[str, str], Dict[str, str]]:
    """返回 {ticker: sector, ...} 和 {ticker: subsector, ...}"""
    sp500 = fetch_sp500_sectors()
    hsi = fetch_hsi_sectors()
    combined = pd.concat([sp500, hsi], ignore_index=True)

    sector_map = dict(zip(combined['ticker'], combined['sector']))
    subsector_map = dict(zip(combined['ticker'], combined['subsector']))
    return sector_map, subsector_map


def load_constituents(market: str) -> List[str]:
    if market == 'sp500':
        path = Path(__file__).parent / 'data' / 'constituents_sp500.txt'
    else:
        path = Path(__file__).parent / 'data' / 'constituents_hsi.txt'
    with open(path, 'r') as f:
        return [line.strip() for line in f if line.strip()]


# ─── 核心掃描邏輯 ────────────────────────────────────────────────────────────

@dataclass
class ScanSignal:
    symbol: str
    sector: str
    subsector: str
    indicator: str
    signal_name: str
    pattern: str
    pattern_confidence: float
    confidence: float       # 綜合信心度 (指標 × 形態)
    win_rate: float         # 歷史勝率（從最佳組合表查得）
    avg_return: float       # 歷史平均回報
    price: float
    date: str
    tier: int               # 1=最高, 2=中, 3=一般
    reasons: str            # 為何入選（文字說明）


def scan_ticker(
    sym: str, df: pd.DataFrame,
    sector: str, subsector: str,
    bullish_index: dict, bearish_index: dict
) -> List[ScanSignal]:
    """
    掃描單一股票，檢測最近 3 根 K 線內所有滿足條件的買入信號。
    """
    signals = []
    if df is None or len(df) < 30:
        return signals

    try:
        ind_df = calculate_all_indicators(df)
    except Exception:
        return signals

    last_idx = len(ind_df) - 1

    # 檢查最近 3 根 K 線（信號可能出現在非最新日期）
    for lookback in range(0, 3):
        check_idx = last_idx - lookback
        if check_idx < 5:
            break

        bull_pis = bullish_index.get(check_idx, [])

        # ── 收集指標信號 ──
        all_ind_signals = []
        all_ind_signals.extend(detect_rsi_signals(ind_df, check_idx))
        all_ind_signals.extend(detect_macd_signals(ind_df, check_idx))
        all_ind_signals.extend(detect_kdj_signals(ind_df, check_idx))
        all_ind_signals.extend(detect_ema_signals(ind_df, check_idx))
        all_ind_signals.extend(detect_bb_signals(ind_df, check_idx))

        for ind_sig in all_ind_signals:
            ind_key = (ind_sig.indicator, ind_sig.name)
            if ind_key not in CORE_BULLISH_INDICATORS or ind_sig.signal_type != 'bullish':
                continue

            # ── 查找匹配的形態 ──
            matched_pattern = None
            matched_conf = 0.0
            for pi in bull_pis:
                if pi.pattern.confidence > matched_conf:
                    matched_pattern = pi.pattern.name
                    matched_conf = pi.pattern.confidence

            # ── 計算信心度 ──
            if matched_pattern:
                conf = min(1.0, ind_sig.confidence * 1.2 + matched_conf * 0.3)
            else:
                conf = ind_sig.confidence * 0.7  # 無形態降權

            # ── 查歷史勝率 ──
            key = (sector, ind_sig.name, matched_pattern or 'None')
            hist = BEST_COMBOS.get(key) or BEST_COMBOS.get(
                (sector, ind_sig.name, 'Support'),
                BEST_COMBOS.get(('Unknown', ind_sig.name, 'Support'),
                (0.50, 0.020, 20))  # 預設值
            )
            win_rate, avg_return, hist_count = hist

            # ── Tier 分級 ──
            if matched_pattern and matched_conf >= 0.7 and hist_count >= 30:
                tier = 1
            elif matched_pattern and hist_count >= 15:
                tier = 2
            else:
                tier = 3

            # ── 構建 reasons ──
            reasons = []
            if sector in ['Information Technology', 'Financials', 'Energy', 'Utilities']:
                reasons.append(f"✅ 強勢Sector（{sector}）")
            if matched_pattern:
                reasons.append(f"形態確認：{matched_pattern}（信心 {matched_conf:.0%}）")
            if hist_count >= 30:
                reasons.append(f"歷史勝率 {win_rate:.0%}（{hist_count}樣本）")
            else:
                reasons.append(f"參考勝率 {win_rate:.0%}（樣本不足 {hist_count}）")

            sig_date = ind_df.index[check_idx]
            date_str = str(sig_date.date()) if hasattr(sig_date, 'date') else str(sig_date)[:10]

            signals.append(ScanSignal(
                symbol=sym,
                sector=sector,
                subsector=subsector,
                indicator=ind_sig.indicator,
                signal_name=ind_sig.name,
                pattern=matched_pattern or 'None',
                pattern_confidence=matched_conf,
                confidence=conf,
                win_rate=win_rate,
                avg_return=avg_return,
                price=float(ind_df['close'].iloc[check_idx]),
                date=date_str,
                tier=tier,
                reasons=' | '.join(reasons),
            ))

    return signals


def scan_market(market: str, tier_filter: Optional[int] = None) -> List[ScanSignal]:
    """掃描整個市場"""
    tickers = load_constituents(market)
    sector_map, subsector_map = load_ticker_sector_map()

    print(f"\n📡 掃描 {market} ({len(tickers)} 隻股票)...")

    # 批量加載數據
    all_data = load_latest_prices(tickers, days=90)

    all_signals = []
    for i, sym in enumerate(tickers):
        df = all_data.get(sym)
        if df is None:
            continue

        sector = sector_map.get(sym, 'Unknown')
        subsector = subsector_map.get(sym, sector)

        bullish_index, _ = build_pattern_index(df)
        sigs = scan_ticker(sym, df, sector, subsector, bullish_index, {})

        all_signals.extend(sigs)
        if (i + 1) % 100 == 0:
            print(f"  ... 已掃描 {i+1} 隻，發現 {len(all_signals)} 個信號")

    if tier_filter is not None:
        all_signals = [s for s in all_signals if s.tier <= tier_filter]

    print(f"  完成：共發現 {len(all_signals)} 個信號")
    return all_signals


def scan_tickers(tickers: List[str]) -> List[ScanSignal]:
    """掃描指定股票列表"""
    sector_map, subsector_map = load_ticker_sector_map()
    all_data = load_latest_prices(tickers, days=90)
    all_signals = []

    for sym in tickers:
        df = all_data.get(sym)
        if df is None:
            print(f"  ⚠️ 無 {sym} 數據")
            continue
        sector = sector_map.get(sym, 'Unknown')
        subsector = subsector_map.get(sym, sector)
        bullish_index, _ = build_pattern_index(df)
        sigs = scan_ticker(sym, df, sector, subsector, bullish_index, {})
        all_signals.extend(sigs)

    return all_signals


# ─── 輸出格式化 ─────────────────────────────────────────────────────────────

def format_signals(signals: List[ScanSignal], top_n: int = 20) -> str:
    """將信號列表格式化為 Telegram 友好文字"""
    if not signals:
        return "📡 今日無符合條件的信號"

    # 按 tier → win_rate → confidence 排序
    signals.sort(key=lambda s: (s.tier, -s.win_rate, -s.confidence))

    lines = []
    lines.append(f"📡 *今日信號掃描* — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"共 {len(signals)} 個信號（顯示 Top {min(top_n, len(signals))}）\n")

    tier_labels = {1: '🔥 Tier-1', 2: '⚡ Tier-2', 3: '📊 Tier-3'}
    current_tier = None

    for i, sig in enumerate(signals[:top_n]):
        if sig.tier != current_tier:
            current_tier = sig.tier
            lines.append(f"\n{tier_labels.get(sig.tier, '')}")
            lines.append("─" * 40)

        price_str = f"${sig.price:.2f}" if sig.symbol.isupper() else f"HK${sig.price:.2f}"
        flag = '🟢' if sig.win_rate >= 0.60 else ('🟡' if sig.win_rate >= 0.45 else '⚪')

        lines.append(
            f"{flag} *{sig.symbol}* ({sig.sector[:15]})\n"
            f"   {sig.indicator}: {sig.signal_name}\n"
            f"   形態: {sig.pattern} | 勝率: {sig.win_rate:.0%} | 參考回報: {sig.avg_return:+.1%}\n"
            f"   {price_str} | {sig.reasons}"
        )

    return '\n'.join(lines)


def signals_to_dataframe(signals: List[ScanSignal]) -> pd.DataFrame:
    """將信號列表轉為 DataFrame（供 Dashboard 使用）"""
    if not signals:
        return pd.DataFrame()
    rows = []
    for s in signals:
        rows.append({
            'symbol': s.symbol,
            'sector': s.sector,
            'subsector': s.subsector,
            'indicator': s.indicator,
            'signal': s.signal_name,
            'pattern': s.pattern,
            'pattern_conf': s.pattern_confidence,
            'confidence': s.confidence,
            'win_rate': s.win_rate,
            'avg_return': s.avg_return,
            'price': s.price,
            'date': s.date,
            'tier': s.tier,
        })
    df = pd.DataFrame(rows)
    df = df.sort_values(['tier', 'win_rate', 'confidence'], ascending=[True, False, False])
    return df.reset_index(drop=True)


# ─── 執行入口 ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='每日信號掃描引擎')
    parser.add_argument('--sp500', action='store_true', help='只掃描 S&P 500')
    parser.add_argument('--hsi', action='store_true', help='只掃描 HSI')
    parser.add_argument('--ticker', type=str, help='掃描指定股票（如 0700.HK）')
    parser.add_argument('--tickers', type=str, help='逗號分隔股票列表')
    parser.add_argument('--tier', type=int, choices=[1, 2, 3], help='只輸出指定 Tier')
    parser.add_argument('--top', type=int, default=20, help='輸出前 N 個')
    parser.add_argument('--output', type=str, help='儲存為 CSV')
    args = parser.parse_args()

    # 決定掃描範圍
    if args.ticker:
        signals = scan_tickers([args.ticker])
    elif args.tickers:
        signals = scan_tickers([t.strip() for t in args.tickers.split(',')])
    elif args.sp500:
        signals = scan_market('sp500', tier_filter=args.tier)
    elif args.hsi:
        signals = scan_market('hsi', tier_filter=args.tier)
    else:
        # 默認：兩個市場都掃
        sigs_sp = scan_market('sp500', tier_filter=args.tier)
        sigs_hk = scan_market('hsi', tier_filter=args.tier)
        signals = sigs_sp + sigs_hk

    if not signals:
        print("📡 今日無符合條件的信號")
        return

    # 輸出
    print(f"\n{'='*70}")
    print(format_signals(signals, top_n=args.top))
    print(f"{'='*70}")

    # 儲存 CSV
    if args.output:
        df = signals_to_dataframe(signals)
        out_path = Path(__file__).parent / args.output
        df.to_csv(out_path, index=False)
        print(f"\n💾 已儲存至 {out_path}")


if __name__ == '__main__':
    main()
