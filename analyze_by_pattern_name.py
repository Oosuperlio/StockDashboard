#!/usr/bin/env python3
"""
形態名稱深度分析
專注於：每種形態配合各指標信號的勝率
"""
import sys
sys.path.insert(0, '.')
import duckdb
import pandas as pd
from collections import defaultdict
from indicator_calculator import calculate_all_indicators
from indicator_signals import detect_all_signals, filter_strong_signals
from pattern_detector import detect_all_patterns

def analyze_market(market, symbols, min_signals=12):
    """分析單一市場"""
    print(f"\n🔍 分析 {market} ({len(symbols)} 隻股票)...")
    
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
    
    # (indicator, signal, direction, pattern_name) -> [returns]
    data = defaultdict(list)
    processed = 0
    
    for sym in symbols:
        sym_df = df[df['symbol'] == sym].copy().reset_index(drop=True)
        if len(sym_df) < 60:
            continue
        
        try:
            ind_df = calculate_all_indicators(sym_df)
            patterns = detect_all_patterns(sym_df)
        except:
            continue
        
        # pattern index -> list of patterns at that index
        pat_dict = defaultdict(list)
        for p in patterns:
            for idx in p.indices:
                for lookback in range(max(0, idx - 3), min(len(sym_df), idx + 4)):
                    pat_dict[lookback].append(p)
        
        # scan indicator signals
        for sig_idx in range(20, len(ind_df)):
            signals = detect_all_signals(ind_df, sig_idx)
            signals = filter_strong_signals(signals)
            
            for sig in signals:
                # find matching pattern
                nearby = pat_dict.get(sig_idx, [])
                matching = [p for p in nearby 
                           if (sig.signal_type == 'bullish' and p.direction == 'bullish') or
                              (sig.signal_type == 'bearish' and p.direction == 'bearish')]
                
                if matching:
                    best_pat = max(matching, key=lambda p: p.confidence)
                    pat_name = best_pat.name
                else:
                    pat_name = '無形態'
                
                # 5-day return
                end = min(sig_idx + 6, len(sym_df))
                if end <= sig_idx + 1:
                    ret = 0.0
                else:
                    ret = (sym_df['close'].iloc[end-1] / sym_df['close'].iloc[sig_idx]) - 1
                
                key = (sig.indicator, sig.name, sig.signal_type, pat_name)
                data[key].append(ret)
        
        processed += 1
        if processed % 100 == 0:
            print(f"  ... 已處理 {processed} 隻股票")
    
    # 計算勝率
    results = []
    for (ind, sig, direction, pat_name), returns in data.items():
        cnt = len(returns)
        if cnt < min_signals:
            continue
        wins = sum(1 for r in returns if r > 0.02)
        results.append({
            'market': market,
            'indicator': ind,
            'signal': sig,
            'direction': direction,
            'pattern_name': pat_name,
            'count': cnt,
            'win_rate': wins / cnt,
            'avg_return': sum(returns) / cnt
        })
    
    return pd.DataFrame(results)

def main():
    sp500 = [s.strip() for s in open('data/constituents_sp500.txt') if s.strip()]
    hsi = [s.strip() for s in open('data/constituents_hsi.txt') if s.strip()]
    
    print("=" * 80)
    print("🔬 形態名稱深度分析 — 哪些形態最能提升勝率？")
    print("=" * 80)
    
    all_results = []
    
    for market, symbols in [('S&P 500', sp500), ('HSI', hsi)]:
        df = analyze_market(market, symbols)
        if len(df) > 0:
            all_results.append(df)
    
    results_df = pd.concat(all_results, ignore_index=True)
    results_df = results_df.sort_values(['market', 'indicator', 'direction', 'win_rate'], ascending=[True, True, True, False])
    
    # 保存
    results_df.to_csv('pattern_name_analysis.csv', index=False)
    print(f"\n💾 已保存至 pattern_name_analysis.csv")
    
    # 打印報告
    for market in ['S&P 500', 'HSI']:
        mdf = results_df[results_df['market'] == market]
        bullish = mdf[mdf['direction'] == 'bullish'].sort_values('win_rate', ascending=False)
        bearish = mdf[mdf['direction'] == 'bearish'].sort_values('win_rate', ascending=False)
        
        print(f"\n{'='*80}")
        print(f"📊 {market} — 形態深度分析")
        print(f"{'='*80}")
        
        print(f"\n🟢 買入信號 (按勝率排序):")
        print(f"{'指標':<6} {'信號':<28} {'形態':<18} {'次數':>5} {'勝率':>8} {'平均回報':>10}")
        print("-" * 75)
        for _, r in bullish.iterrows():
            if r['win_rate'] < 0.25 and r['count'] < 20:
                continue
            print(f"{r['indicator']:<6} {r['signal'][:26]:<28} {r['pattern_name']:<18} {r['count']:>5} {r['win_rate']*100:>7.1f}% {r['avg_return']*100:>9.2f}%")
        
        print(f"\n🔴 賣出信號 (按勝率排序):")
        print(f"{'指標':<6} {'信號':<28} {'形態':<18} {'次數':>5} {'勝率':>8} {'平均回報':>10}")
        print("-" * 75)
        for _, r in bearish.iterrows():
            if r['win_rate'] < 0.25 and r['count'] < 20:
                continue
            print(f"{r['indicator']:<6} {r['signal'][:26]:<28} {r['pattern_name']:<18} {r['count']:>5} {r['win_rate']*100:>7.1f}% {r['avg_return']*100:>9.2f}%")
        
        # 形態提升對比
        print(f"\n{'='*80}")
        print(f"📌 {market} — 形態 vs 無形態 勝率提升幅度")
        print(f"{'='*80}")
        print(f"{'指標':<6} {'信號':<28} {'形態':<18} {'無形態勝率':>10} {'有形態勝率':>10} {'提升':>8}")
        print("-" * 80)
        
        for ind in ['RSI', 'BB', 'MACD', 'KDJ', 'EMA']:
            for sig_set in bullish[bullish['indicator'] == ind]['signal'].unique():
                no_pat = bullish[(bullish['indicator'] == ind) & 
                                (bullish['signal'] == sig_set) & 
                                (bullish['pattern_name'] == '無形態')]
                if len(no_pat) == 0:
                    continue
                no_pat_wr = no_pat['win_rate'].values[0]
                no_pat_cnt = no_pat['count'].values[0]
                
                has_pat = bullish[(bullish['indicator'] == ind) & 
                                 (bullish['signal'] == sig_set) & 
                                 (bullish['pattern_name'] != '無形態')].sort_values('win_rate', ascending=False)
                
                for _, r in has_pat.iterrows():
                    if r['count'] < 12:
                        continue
                    lift = (r['win_rate'] - no_pat_wr) * 100
                    arrow = '🔺' if lift > 0 else '🔻'
                    print(f"{ind:<6} {sig_set[:26]:<28} {r['pattern_name']:<18} {no_pat_wr*100:>9.1f}% {r['win_rate']*100:>9.1f}% {arrow} {lift:>+6.1f}%")
        
        print()

if __name__ == '__main__':
    main()
