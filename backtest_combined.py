"""
backtest_combined.py — 技術指標 + 形態複合策略回測引擎
======================================================
目標：測試「指標信號 + 形態確認」複合策略是否能提升勝率

優化版：預先計算形態，再與指標信號匹配（避免重複掃描）
"""

import sys
import os
import argparse
import duckdb
import warnings
warnings.filterwarnings('ignore')

from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Set

import pandas as pd
import numpy as np

# 導入本地模組
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
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


# ─────────────────────────────────────────────
# 參數配置
# ─────────────────────────────────────────────
FORWARD_DAYS = 5
THRESHOLD = 0.02
MIN_SIGNALS = 10
MIN_PATTERN_CONFIDENCE = 0.5

# ─────────────────────────────────────────────
# 成交量確認因子（因子①）
# ─────────────────────────────────────────────
VOL_MA_PERIOD = 20           # 成交量均線週期
VOL_SPIKE_TODAY = 1.5        # 形態出現日：成交量 > MA 的倍數
VOL_SPIKE_NEXT = 1.2          # 形態次日：成交量 > MA 的倍數（確認不是脈衝）

# 強指標信號（高信心度，勝率較好）
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

# 形態名單
BULLISH_PATTERNS = {
    'Support', 'Morning Star', 'Bullish Engulfing',
    'Bull Flag', 'Hammer', 'Ascending Triangle', 'Bullish Harami'
}

BEARISH_PATTERNS = {
    'Resistance', 'Evening Star', 'Bearish Engulfing',
    'Bear Flag', 'Shooting Star', 'Descending Triangle', 'Bearish Harami'
}


# ─────────────────────────────────────────────
# 形態預先計算（per stock）
# ─────────────────────────────────────────────

@dataclass
class PatternIndex:
    """形態索引：形態覆蓋的 K 線位置"""
    pattern: Pattern

    def covers(self, idx: int) -> bool:
        return idx in self.pattern.indices


def build_pattern_index(df: pd.DataFrame) -> Tuple[
    dict,  # idx → list of bullish PatternIndex
    dict,  # idx → list of bearish PatternIndex
    pd.DataFrame  # df with vol_ma20 added
]:
    """
    預先計算形態並建立索引
    返回: (bullish_index, bearish_index, df_with_vol_ma)
    其中 index[idx] = [PatternIndex, ...] 包含該位置的看漲/看跌形態
    """
    # ── 計算成交量均線（因子①）────────────────────
    df = df.copy()
    df['vol_ma20'] = df['volume'].rolling(VOL_MA_PERIOD, min_periods=10).mean()

    bullish_index: dict = defaultdict(list)
    bearish_index: dict = defaultdict(list)

    # 單根 K 線形態（用 idx 參數）
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

    # 價格形態（整體）
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


# ─────────────────────────────────────────────
# 數據加載
# ─────────────────────────────────────────────

def load_stock_data(symbol: str) -> pd.DataFrame:
    """從 DuckDB 加載股價數據"""
    db_path = Path(__file__).parent / 'data' / 'prices.ddb'
    conn = duckdb.connect(str(db_path), read_only=True)

    df = pd.read_sql_query("""
        SELECT trade_date as date, symbol, open, high, low, close, volume
        FROM stock_prices
        WHERE symbol = ?
        ORDER BY trade_date ASC
    """, conn, params=(symbol,))
    conn.close()

    if df.empty:
        return df

    df['date'] = pd.to_datetime(df['date'])
    df.set_index('date', inplace=True)
    return df


def load_constituents(market: str) -> list:
    """加載成分股列表"""
    if market == 'sp500':
        path = Path(__file__).parent / 'data' / 'constituents_sp500.txt'
    else:
        path = Path(__file__).parent / 'data' / 'constituents_hsi.txt'

    with open(path, 'r') as f:
        return [line.strip() for line in f if line.strip()]


# ─────────────────────────────────────────────
# 複合信號檢測
# ─────────────────────────────────────────────

@dataclass
class CombinedSignal:
    indicator: str
    indicator_signal: str
    indicator_direction: str
    pattern: str
    pattern_confidence: float
    confidence: float
    volume_confirmed: bool = False  # 因子①
    metadata: dict = field(default_factory=dict)


def detect_combined_signals_at_idx(
    df: pd.DataFrame,
    idx: int,
    bullish_index: dict,
    bearish_index: dict
) -> List[CombinedSignal]:
    """檢測指定 K 線的複合信號（含成交量確認過濾）"""
    signals = []

    # ── 因子①：成交量確認過濾 ──────────────────────
    # 需要 idx+1 有次日數據才能做兩日確認
    vol_today_ok = False
    vol_next_ok = False
    if idx + 1 < len(df):
        vol_today = df['volume'].iloc[idx]
        vol_ma = df['vol_ma20'].iloc[idx]
        vol_next = df['volume'].iloc[idx + 1]
        vol_ma_next = df['vol_ma20'].iloc[idx + 1]
        if vol_ma > 0:
            vol_today_ok = vol_today >= vol_ma * VOL_SPIKE_TODAY
        if vol_ma_next > 0:
            vol_next_ok = vol_next >= vol_ma_next * VOL_SPIKE_NEXT

    # 必須同時滿足：形態日放量 AND 次日跟進放量
    vol_confirmed = vol_today_ok and vol_next_ok

    # 1. 收集指標信號
    all_ind_signals = []
    all_ind_signals.extend(detect_rsi_signals(df, idx))
    all_ind_signals.extend(detect_macd_signals(df, idx))
    all_ind_signals.extend(detect_kdj_signals(df, idx))
    all_ind_signals.extend(detect_ema_signals(df, idx))
    all_ind_signals.extend(detect_bb_signals(df, idx))

    # 2. 獲取該位置的形態
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

        # 查找匹配的形態
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

        # 構建信號（全部保留，不因成交量不足而過濾）
        if matched_pattern:
            conf = min(1.0, ind_sig.confidence * 1.2 + matched_confidence * 0.3)
            signals.append(CombinedSignal(
                indicator=ind_sig.indicator,
                indicator_signal=ind_sig.name,
                indicator_direction=direction,
                pattern=matched_pattern,
                pattern_confidence=matched_confidence,
                confidence=conf,
                volume_confirmed=vol_confirmed,  # 因子①：成交量是否確認
                metadata={'pattern_confidence': matched_confidence}
            ))
        else:
            # 純指標
            signals.append(CombinedSignal(
                indicator=ind_sig.indicator,
                indicator_signal=ind_sig.name,
                indicator_direction=direction,
                pattern='None',
                pattern_confidence=0.0,
                confidence=ind_sig.confidence * 0.9,
                volume_confirmed=vol_confirmed,  # 因子①
                metadata={}
            ))

    return signals


# ─────────────────────────────────────────────
# 回測引擎
# ─────────────────────────────────────────────

def backtest_signal(df: pd.DataFrame, signal_date_idx: int,
                    direction: str, forward_days: int = FORWARD_DAYS) -> Optional[dict]:
    if signal_date_idx + forward_days >= len(df):
        return None

    entry_price = df['close'].iloc[signal_date_idx]
    exit_price = df['close'].iloc[signal_date_idx + forward_days]

    ret = (exit_price - entry_price) / entry_price
    if direction == 'bearish':
        ret = -ret

    return {
        'return': ret,
        'is_success': ret > THRESHOLD
    }


def backtest_stock(symbol: str) -> Tuple[List[dict], List[dict]]:
    """
    回測單一股票
    返回: (複合信號結果, 純指標信號結果)
    """
    df = load_stock_data(symbol)
    if df.empty or len(df) < 60:
        return [], []

    # 計算指標
    df = calculate_all_indicators(df)

    # 預先計算形態索引（返回 df 含 vol_ma20）
    bullish_index, bearish_index, df = build_pattern_index(df)

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
                'date': df.index[idx],
                'indicator': sig.indicator,
                'signal': sig.indicator_signal,
                'direction': sig.indicator_direction,
                'confidence': sig.confidence,
                'pattern': sig.pattern,
                'pattern_confidence': sig.pattern_confidence,
                'volume_confirmed': sig.volume_confirmed,  # 因子①
                **result
            }

            if sig.pattern != 'None':
                combined_results.append(base)
            else:
                indicator_only_results.append(base)

    return combined_results, indicator_only_results


def aggregate_comparison(combined: list, indicator_only: list,
                          min_count: int = MIN_SIGNALS) -> pd.DataFrame:
    """
    聚合並比較複合 vs 純指標
    新增：按 volume_confirmed（因子①）分組，展示成交量確認對勝率的影響
    """
    rows = []

    def make_key(r):
        # 4維分組：(indicator, signal, direction, has_pattern, vol_confirmed)
        return (r['indicator'], r['signal'], r['direction'],
                r['pattern'] != 'None', r.get('volume_confirmed', False))

    agg = defaultdict(lambda: {'count': 0, 'successes': 0, 'total_return': 0.0})

    for r in combined + indicator_only:
        key = make_key(r)
        agg[key]['count'] += 1
        agg[key]['total_return'] += r['return']
        if r['is_success']:
            agg[key]['successes'] += 1

    for (indicator, signal, direction, has_pattern, vol_confirmed), stats in agg.items():
        if stats['count'] < min_count:
            continue
        wr = stats['successes'] / stats['count']
        avg_ret = stats['total_return'] / stats['count']
        rows.append({
            'indicator': indicator,
            'signal': signal,
            'direction': direction,
            'strategy': '✅ 複合' if has_pattern else '⚪ 純指標',
            'volume_confirmed': '🔔 有量確認' if vol_confirmed else '⚪ 無量確認',
            'count': stats['count'],
            'successes': stats['successes'],
            'win_rate': wr,
            'avg_return': avg_ret,
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        # Add improvement column (vs 純指標 + 無量確認)
        df['improvement'] = 0.0
        for idx, row in df[df['strategy'] == '✅ 複合'].iterrows():
            base_rows = df[(df['signal'] == row['signal']) &
                          (df['strategy'] == '⚪ 純指標') &
                          (df['volume_confirmed'] == row['volume_confirmed']) &
                          (df['direction'] == row['direction'])]
            if not base_rows.empty:
                base_wr = base_rows.iloc[0]['win_rate']
                df.loc[idx, 'improvement'] = row['win_rate'] - base_wr

        # Sort: indicator → signal → volume_confirmed → strategy
        vol_order = {'🔔 有量確認': 0, '⚪ 無量確認': 1}
        df['_vol_order'] = df['volume_confirmed'].map(vol_order)
        df = df.sort_values(
            ['indicator', 'signal', '_vol_order', 'strategy'],
            ascending=[True, True, True, False]
        )
        df = df.drop(columns=['_vol_order']).reset_index(drop=True)

    return df


def run_market_backtest(market: str) -> pd.DataFrame:
    """回測整個市場"""
    tickers = load_constituents(market)
    market_name = 'S&P 500' if market == 'sp500' else 'HSI'
    print(f"\n🔍 開始回測 {market_name} ({len(tickers)} 隻股票)...")

    all_combined = []
    all_indicator_only = []
    processed = skipped = 0

    for i, symbol in enumerate(tickers):
        combined, ind_only = backtest_stock(symbol)

        if not combined and not ind_only:
            skipped += 1
        else:
            processed += 1

        all_combined.extend(combined)
        all_indicator_only.extend(ind_only)

        if (i + 1) % 50 == 0:
            print(f"  ... 已處理 {i + 1} 隻股票")

    print(f"\n完成: 處理 {processed} 隻, 跳過 {skipped} 隻")
    print(f"  複合信號: {len(all_combined)} 個")
    print(f"  純指標信號: {len(all_indicator_only)} 個")

    if not all_combined and not all_indicator_only:
        return pd.DataFrame()

    return aggregate_comparison(all_combined, all_indicator_only)


# ─────────────────────────────────────────────
# 輸出
# ─────────────────────────────────────────────

def print_comparison(results: pd.DataFrame, market_name: str):
    """
    輸出複合策略回測結果，含因子①（成交量確認）維度
    同時標記「原本 ≥70% 的信號是否被保留」
    """
    print(f"\n{'='*110}")
    print(f"  📊 {market_name} — 複合策略 (指標+形態+成交量) 回測結果")
    print(f"    因子①：形態日放量 × 1.5× + 次日跟進 × 1.2×")
    print(f"{'='*110}")

    if results.empty:
        print("  無足夠數據")
        return

    has_combo = results[results['strategy'] == '✅ 複合'].copy()
    if has_combo.empty:
        print("\n  ⚠️ 無足夠的複合信號樣本")
        return

    # ── ① 成交量維度對比 ──────────────────────────
    print(f"\n{'─'*110}")
    print(f"  📌 因子①：成交量確認對勝率的影響")
    print(f"{'─'*110}")
    print(f"  {'指標':<6} {'信號':<30} {'成交量':<14} {'次數':>6} {'勝率':>8} {'平均回報':>10} {'vs無確認':>10}")
    print(f"{'─'*110}")

    for _, row in has_combo.sort_values('win_rate', ascending=False).iterrows():
        imp = row['improvement']
        imp_str = f"{imp:+.1%}" if imp != 0 else "-"
        flag = "🛡️" if row['win_rate'] >= 0.70 else "  "
        print(f"  {flag}{row['indicator']:<4} {row['signal'][:30]:<30} {row['volume_confirmed']:<14} "
              f"{row['count']:>6} {row['win_rate']:>7.1%} {row['avg_return']:>+9.2%} {imp_str:>10}")

    # ── ② 原本 ≥70% 的信號是否被保留 ─────────────
    print(f"\n{'─'*110}")
    print(f"  🛡️ 原本勝率 ≥70% 的信號（加成交量後是否保留）")
    print(f"{'─'*110}")

    high_wr = has_combo[has_combo['win_rate'] >= 0.70].copy()
    if high_wr.empty:
        print("  （無原本 ≥70% 的信號）")
    else:
        print(f"  {'指標':<6} {'信號':<30} {'成交量':<14} {'次數':>6} {'勝率':>8}")
        print(f"  {'─'*80}")
        for _, row in high_wr.sort_values('win_rate', ascending=False).iterrows():
            print(f"  {row['indicator']:<6} {row['signal'][:30]:<30} {row['volume_confirmed']:<14} "
                  f"{row['count']:>6} {row['win_rate']:>7.1%}")

    # ── ③ 純指標基准 ─────────────────────────────
    print(f"\n{'─'*110}")
    print(f"  📌 純指標信號胜率（對比基准）:")
    print(f"{'─'*110}")

    ind_only = results[results['strategy'] == '⚪ 純指標'].copy()
    for _, row in ind_only.sort_values('win_rate', ascending=False).iterrows():
        print(f"  {row['indicator']:<6} {row['signal'][:30]:<30} {row['volume_confirmed']:<14} "
              f"{row['count']:>6} {row['win_rate']:>7.1%} {row['avg_return']:>+9.2%}")


def main():
    parser = argparse.ArgumentParser(description='複合策略回測：技術指標 + 形態')
    parser.add_argument('--sp500', action='store_true')
    parser.add_argument('--hsi', action='store_true')
    parser.add_argument('--stock', type=str)
    parser.add_argument('--output', type=str)
    args = parser.parse_args()

    markets = []
    if args.sp500:
        markets.append(('sp500', 'S&P 500'))
    elif args.hsi:
        markets.append(('hsi', 'HSI'))
    else:
        markets = [('sp500', 'S&P 500'), ('hsi', 'HSI')]

    all_results = []

    for market, name in markets:
        if args.stock:
            combined, ind_only = backtest_stock(args.stock)
            result = aggregate_comparison(combined, ind_only)
            if not result.empty:
                result['market'] = name
                all_results.append(result)
        else:
            result = run_market_backtest(market)
            if not result.empty:
                result['market'] = name
                all_results.append(result)

    if not all_results:
        print("無回測結果")
        return

    combined_df = pd.concat(all_results, ignore_index=True)

    for name in combined_df['market'].unique():
        mdf = combined_df[combined_df['market'] == name]
        print_comparison(mdf, name)

    if args.output:
        combined_df.to_csv(args.output, index=False)
        print(f"\n💾 已保存至: {args.output}")
    else:
        path = Path(__file__).parent / 'backtest_combined_results.csv'
        combined_df.to_csv(path, index=False)
        print(f"\n💾 已保存至: {path}")


if __name__ == '__main__':
    main()
