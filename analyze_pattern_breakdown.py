#!/usr/bin/env python3
"""
形態 + 指標複合策略深度分析
按具體形態名稱分組，計算每種形態對勝率的貢獻
"""
import sys
sys.path.insert(0, '.')

import duckdb
import pandas as pd
import numpy as np
from collections import defaultdict
from indicator_calculator import calculate_all_indicators
from indicator_signals import detect_all_signals, filter_strong_signals
from pattern_detector import detect_all_patterns, Pattern

def load_data(symbols, market):
    """加載股票數據"""
    con = duckdb.connect('data/prices.ddb', read_only=True)
    placeholders = ','.join(['?' for _ in symbols])
    df = con.execute(f"""
        SELECT symbol, trade_date, open, high, low, close, volume
        FROM stock_prices
        WHERE symbol IN ({placeholders})
        ORDER BY symbol, trade_date
    """, symbols).df()
    con.close()
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    return df

def run_pattern_breakdown_backtest(symbols, market, min_signals=15):
    """
    回測並按具體形態名稱分組
    追蹤：(指標, 信號, 方向, 形態名稱) -> successes/count
    """
    all_results = []
    
    # 加載所有數據
    df = load_data(symbols, market)
    symbols_loaded = df['symbol'].unique()
    print(f"📊 {market} 回測開始，共 {len(symbols_loaded)} 隻股票...")
    
    total = len(symbols_loaded)
    for i, sym in enumerate(symbols_loaded):
        if (i + 1) % 100 == 0:
            print(f"  ... 已處理 {i+1} 隻股票")
        
        sym_df = df[df['symbol'] == sym].copy().reset_index(drop=True)
        if len(sym_df) < 60:
            continue
        
        # 計算指標和形態
        try:
            ind_df = calculate_all_indicators(sym_df)
            patterns = detect_all_patterns(sym_df)
        except:
            continue
        
        # 建立形態字典：(idx -> pattern_list)
        pattern_dict = defaultdict(list)
        for p in patterns:
            for idx in range(max(0, p.idx - 3), min(len(sym_df), p.idx + 4)):
                pattern_dict[idx].append(p)
        
        # 檢測指標信號 (每個idx掃描一次)
        for sig_idx in range(20, len(ind_df)):
            signals = detect_all_signals(ind_df, sig_idx)
            signals = filter_strong_signals(signals)
            
            for sig in signals:
                # 找形態名稱列表
                nearby_patterns = pattern_dict.get(sig_idx, [])
                
                if not nearby_patterns:
                    pattern_name = '無形態'
                else:
                    # 選擇方向匹配且最高置信度的形態
                    matching = [p for p in nearby_patterns 
                               if (sig.direction == 'bullish' and p.direction == 'bullish') or
                                  (sig.direction == 'bearish' and p.direction == 'bearish')]
                    if not matching:
                        pattern_name = '方向衝突'
                    else:
                        best = max(matching, key=lambda p: p.confidence)
                        pattern_name = best.name
                
                # 計算未來5日回報
                future_end = min(sig_idx + 6, len(sym_df))
                future_prices = sym_df['close'].iloc[sig_idx:future_end]
                if len(future_prices) < 2:
                    ret = 0.0
                else:
                    ret = (future_prices.iloc[-1] / future_prices.iloc[0]) - 1
                
                success = 1 if ret > 0.02 else 0
                
                all_results.append({
                    'symbol': sym,
                    'indicator': sig.indicator,
                    'signal': sig.signal,
                    'direction': sig.direction,
                    'pattern_name': pattern_name,
                    'confidence': sig.confidence,
                    'return_5d': ret,
                    'success': success,
                    'market': market
                })
    
    results_df = pd.DataFrame(all_results)
    return results_df

def aggregate_by_pattern(results_df, market, min_signals=15):
    """按 (指標, 信號, 形態名稱) 分組計算勝率"""
    grouped = results_df.groupby(['indicator', 'signal', 'direction', 'pattern_name']).agg(
        count=('success', 'count'),
        successes=('success', 'sum'),
        avg_return=('return_5d', 'mean')
    ).reset_index()
    
    grouped['win_rate'] = grouped['successes'] / grouped['count']
    grouped['market'] = market
    grouped = grouped[grouped['count'] >= min_signals]
    grouped = grouped.sort_values('win_rate', ascending=False)
    
    return grouped

def main():
    # 讀取成分股
    sp500 = [s.strip() for s in open('data/constituents_sp500.txt') if s.strip()]
    hsi = [s.strip() for s in open('data/constituents_hsi.txt') if s.strip()]
    
    print("=" * 80)
    print("🔬 形態 + 指標複合策略深度分析")
    print("   按具體形態名稱分組 | 最低 {} 個信號樣本".format(15))
    print("=" * 80)
    
    for market, symbols in [('S&P 500', sp500), ('HSI', hsi)]:
        print(f"\n🔍 開始分析 {market}...")
        results = run_pattern_breakdown_backtest(symbols, market)
        
        # 按形態分組
        agg = aggregate_by_pattern(results, market)
        
        print(f"\n{'='*80}")
        print(f"📊 {market} — 形態 + 指標複合策略勝率明細")
        print(f"{'='*80}")
        print(f"\n{'指標':<6} {'信號':<30} {'形態':<18} {'次數':>6} {'勝率':>8} {'平均回報':>10}")
        print("-" * 80)
        
        for _, r in agg.iterrows():
            print(f"{r['indicator']:<6} {r['signal'][:28]:<30} {r['pattern_name']:<18} {r['count']:>6} {r['win_rate']*100:>7.1f}% {r['avg_return']*100:>9.2f}%")
        
        # 保存結果
        agg.to_csv(f'pattern_breakdown_{market.replace(" ", "_").lower()}.csv', index=False)
        print(f"\n💾 已保存至 pattern_breakdown_{market.replace(' ', '_').lower()}.csv")
    
    # 對比分析
    print("\n" + "=" * 80)
    print("📌 形態對勝率提升效果分析")
    print("=" * 80)
    
    for market in ['S&P 500', 'HSI']:
        df1 = pd.read_csv(f'pattern_breakdown_{market.replace(" ", "_").lower()}.csv')
        
        # 只看買入信號
        df1 = df1[df1['direction'] == 'bullish']
        
        # 按指標分組對比
        print(f"\n🏛️ {market}:")
        
        for ind in ['RSI', 'BB', 'MACD', 'KDJ', 'EMA']:
            ind_df = df1[df1['indicator'] == ind]
            if len(ind_df) == 0:
                continue
            
            no_pattern = ind_df[ind_df['pattern_name'] == '無形態']
            has_pattern = ind_df[ind_df['pattern_name'] != '無形態']
            
            if len(no_pattern) == 0 or len(has_pattern) == 0:
                continue
            
            no_p_wr = no_pattern['win_rate'].values[0]
            no_p_cnt = no_pattern['count'].values[0]
            
            print(f"\n  {ind}:")
            print(f"    無形態: 勝率 {no_p_wr*100:.1f}% (n={no_p_cnt})")
            
            for _, r in has_pattern.sort_values('win_rate', ascending=False).iterrows():
                if r['count'] < 15:
                    continue
                lift = (r['win_rate'] - no_p_wr) * 100
                arrow = '🔺' if lift > 0 else '🔻'
                print(f"    {r['pattern_name']:<18}: 勝率 {r['win_rate']*100:.1f}% (n={r['count']})  {arrow} {lift:+.1f}%")

if __name__ == '__main__':
    main()
