#!/usr/bin/env python3
"""
Equity Curve Simulation
根據最優止盈止損策略，模擬 30 筆交易的本金曲線
"""
import sys
sys.path.insert(0, '.')
import duckdb
import pandas as pd
import numpy as np
from collections import defaultdict
from indicator_calculator import calculate_all_indicators
from indicator_signals import detect_all_signals, filter_strong_signals
from pattern_detector import detect_all_patterns
import random

# 策略配置
STRATEGIES = [
    {'name': 'RSI+Support (HSI)', 'ind': 'RSI', 'sig': 'RSI 超賣區域 (30)', 'pat': 'Support', 'dir': 'bullish',
     'tp': 0.10, 'sl': 0.02, 'market': 'HSI'},
    {'name': 'BB+Support (HSI)', 'ind': 'BB', 'sig': 'BB 跌破下軌 (超賣)', 'pat': 'Support', 'dir': 'bullish',
     'tp': 0.08, 'sl': 0.05, 'market': 'HSI'},
    {'name': 'RSI+Support (SPX)', 'ind': 'RSI', 'sig': 'RSI 超賣區域 (30)', 'pat': 'Support', 'dir': 'bullish',
     'tp': 0.06, 'sl': 0.05, 'market': 'S&P 500'},
    {'name': 'BB+Support (SPX)', 'ind': 'BB', 'sig': 'BB 跌破下軌 (超賣)', 'pat': 'Support', 'dir': 'bullish',
     'tp': 0.06, 'sl': 0.05, 'market': 'S&P 500'},
    {'name': 'EMA多頭+BullFlag (SPX)', 'ind': 'EMA', 'sig': 'EMA 多頭排列 (20>50>200)', 'pat': 'Bull Flag', 'dir': 'bullish',
     'tp': 0.15, 'sl': 0.05, 'market': 'S&P 500'},
    {'name': 'RSI+DoubleBottom (SPX)', 'ind': 'RSI', 'sig': 'RSI 超賣區域 (30)', 'pat': 'Double Bottom', 'dir': 'bullish',
     'tp': 0.06, 'sl': 0.05, 'market': 'S&P 500'},
    {'name': 'KDJ+MorningStar (HSI)', 'ind': 'KDJ', 'sig': 'KDJ J 值極低 (<0)', 'pat': 'Morning Star', 'dir': 'bullish',
     'tp': 0.06, 'sl': 0.05, 'market': 'HSI'},
    {'name': 'BB+BullishEngulfing (SPX)', 'ind': 'BB', 'sig': 'BB 跌破下軌 (超賣)', 'pat': 'Bullish Engulfing', 'dir': 'bullish',
     'tp': 0.04, 'sl': 0.05, 'market': 'S&P 500'},
]

def load_data():
    con = duckdb.connect('data/prices.ddb', read_only=True)
    sp500 = [s.strip() for s in open('data/constituents_sp500.txt') if s.strip()]
    hsi = [s.strip() for s in open('data/constituents_hsi.txt') if s.strip()]
    
    placeholders_sp = ','.join(['?' for _ in sp500])
    placeholders_hsi = ','.join(['?' for _ in hsi])
    
    df_sp = con.execute(f"""
        SELECT symbol, trade_date, open, high, low, close, volume
        FROM stock_prices WHERE symbol IN ({placeholders_sp})
        ORDER BY symbol, trade_date
    """, sp500).df()
    
    df_hsi = con.execute(f"""
        SELECT symbol, trade_date, open, high, low, close, volume
        FROM stock_prices WHERE symbol IN ({placeholders_hsi})
        ORDER BY symbol, trade_date
    """, hsi).df()
    
    con.close()
    return pd.concat([df_sp, df_hsi], ignore_index=True)

def collect_trades(df, strat, max_trades=100):
    """收集某策略的所有歷史交易"""
    ind_name, sig_name, pat_name, direction = strat['ind'], strat['sig'], strat['pat'], strat['dir']
    
    all_trades = []
    symbols = df['symbol'].unique()
    
    for sym in symbols:
        sym_df = df[df['symbol'] == sym].copy().reset_index(drop=True)
        if len(sym_df) < 60:
            continue
        
        try:
            ind_df = calculate_all_indicators(sym_df)
            patterns = detect_all_patterns(sym_df)
        except:
            continue
        
        pat_dict = defaultdict(list)
        for p in patterns:
            for idx in p.indices:
                for lookback in range(max(0, idx - 3), min(len(sym_df), idx + 4)):
                    pat_dict[lookback].append(p)
        
        for sig_idx in range(20, len(ind_df) - 10):
            signals = detect_all_signals(ind_df, sig_idx)
            signals = [s for s in signals
                      if s.indicator == ind_name
                      and s.name == sig_name
                      and s.signal_type == direction]
            if not signals:
                continue
            
            nearby = pat_dict.get(sig_idx, [])
            matching = [p for p in nearby if p.direction == 'bullish' and p.name == pat_name]
            if not matching:
                continue
            
            entry_price = sym_df['close'].iloc[sig_idx]
            tp = strat['tp']
            sl = strat['sl']
            holding_days = 5
            
            exit_price = None
            exit_ret = 0.0
            exit_type = 'timeout'
            
            for d in range(1, holding_days + 1):
                if sig_idx + d >= len(sym_df):
                    break
                day_high = sym_df['high'].iloc[sig_idx + d]
                day_low = sym_df['low'].iloc[sig_idx + d]
                
                high_ret = (day_high / entry_price) - 1
                low_ret = (day_low / entry_price) - 1
                
                if high_ret >= tp:
                    exit_ret = tp
                    exit_type = 'tp'
                    break
                elif low_ret <= -sl:
                    exit_ret = -sl
                    exit_type = 'sl'
                    break
            
            if exit_type == 'timeout':
                exit_ret = (sym_df['close'].iloc[min(sig_idx + holding_days, len(sym_df)-1)] / entry_price) - 1
            
            all_trades.append({
                'symbol': sym,
                'entry_date': sym_df['trade_date'].iloc[sig_idx],
                'entry_idx': sig_idx,
                'return': exit_ret,
                'exit_type': exit_type,
            })
    
    return all_trades

def simulate_equity_curve(trades, initial_capital=100000, n_simulations=1000, n_trades=30):
    """
    Monte Carlo 模擬：隨機抽取 n_trades 筆交易，計算本金曲線
    """
    results = []
    
    for sim in range(n_simulations):
        if len(trades) < n_trades:
            continue
        
        # 隨機抽樣（带替換，模擬真實隨機進場）
        sampled = random.choices(trades, k=n_trades)
        
        capital = initial_capital
        curve = [capital]
        
        for trade in sampled:
            capital = capital * (1 + trade['return'])
            curve.append(capital)
        
        final_return = (capital / initial_capital - 1) * 100
        max_dd = 0.0
        peak = initial_capital
        
        for c in curve:
            if c > peak:
                peak = c
            dd = (c - peak) / peak
            if dd < max_dd:
                max_dd = dd
        
        results.append({
            'sim': sim,
            'final_capital': capital,
            'total_return': final_return,
            'max_drawdown': max_dd * 100,
            'curve': curve,
            'trade_returns': [t['return'] for t in sampled],
        })
    
    return results

def print_report(name, strat, results, n_trades):
    if not results:
        return
    
    total_returns = [r['total_return'] for r in results]
    max_dds = [r['max_drawdown'] for r in results]
    final_capitals = [r['final_capital'] for r in results]
    
    # 排序
    total_returns_sorted = sorted(total_returns)
    final_capitals_sorted = sorted(final_capitals)
    
    p5_idx = int(len(results) * 0.05)
    p50_idx = int(len(results) * 0.50)
    p95_idx = int(len(results) * 0.95)
    
    print(f"\n{'='*70}")
    print(f"📊 {name}")
    print(f"   止盈: {strat['tp']*100:.0f}% | 止損: {strat['sl']*100:.0f}% | 模擬次數: {len(results)}")
    print(f"{'='*70}")
    print(f"   初始本金: ${'{:,.0f}'.format(100000)} | 交易筆數: {n_trades}")
    print(f"   {'描述':<12} {'總回報':>10} {'本金':>14} {'最大回撤':>10}")
    print(f"   {'-'*50}")
    print(f"   {'5%分位 (極差)':<12} {total_returns_sorted[p5_idx]:>+9.1f}% {'$'+str(int(final_capitals_sorted[p5_idx])):>13} {max_dds[p5_idx]:>+9.1f}%")
    print(f"   {'50%分位 (中位)':<12} {total_returns_sorted[p50_idx]:>+9.1f}% {'$'+str(int(final_capitals_sorted[p50_idx])):>13} {max_dds[p50_idx]:>+9.1f}%")
    print(f"   {'95%分位 (極佳)':<12} {total_returns_sorted[p95_idx]:>+9.1f}% {'$'+str(int(final_capitals_sorted[p95_idx])):>13} {max_dds[p95_idx]:>+9.1f}%")
    print(f"   {'平均':<12} {np.mean(total_returns):>+9.1f}% {'$'+str(int(np.mean(final_capitals))):>13} {np.mean(max_dds):>+9.1f}%")
    
    # 勝率
    win_rate = sum(1 for r in results if r['total_return'] > 0) / len(results) * 100
    print(f"\n   盈利概率: {win_rate:.1f}% ({sum(1 for r in results if r['total_return'] > 0)}/{len(results)})")
    
    # 顯示一條典型曲線（最接近中位數的）
    median_sim = min(results, key=lambda r: abs(r['total_return'] - total_returns_sorted[p50_idx]))
    print(f"\n   📈 典型中位數本金曲線（前{len(median_sim['curve'])-1}筆）:")
    print(f"   起始: $100,000", end='')
    for i, c in enumerate(median_sim['curve'][1:], 1):
        if i % 5 == 0 or i == len(median_sim['curve'])-1:
            print(f" → ${c:,.0f}", end='')
    print()

def main():
    random.seed(42)
    np.random.seed(42)
    
    print("=" * 70)
    print("🎲 Equity Curve Monte Carlo 模擬")
    print("   初始本金: $100,000 | 持有: 5個交易日 | 模擬次數: 1,000")
    print("=" * 70)
    
    df = load_data()
    
    n_trades_list = [15, 20, 25, 30]
    
    all_strat_results = {}
    
    for strat in STRATEGIES:
        print(f"\n⏳ 收集 {strat['name']} 歷史交易...", end=' ', flush=True)
        trades = collect_trades(df, strat)
        print(f"完成 ({len(trades)} 筆交易)")
        
        if len(trades) < 20:
            print(f"   交易不足，跳過")
            continue
        
        all_strat_results[strat['name']] = {
            'strat': strat,
            'trades': trades,
        }
    
    # 按交易筆數分別報告
    for n_trades in n_trades_list:
        print(f"\n\n{'#'*70}")
        print(f"# 模擬 {n_trades} 筆交易後的結果")
        print(f"{'#'*70}")
        
        best_ev = -999
        best_name = ''
        best_strat = None
        
        for name, data in all_strat_results.items():
            results = simulate_equity_curve(
                data['trades'],
                initial_capital=100000,
                n_simulations=1000,
                n_trades=n_trades
            )
            print_report(name, data['strat'], results, n_trades)
            
            median_return = sorted([r['total_return'] for r in results])[500]
            if median_return > best_ev:
                best_ev = median_return
                best_name = name
                best_strat = data['strat']
        
        print(f"\n{'='*70}")
        print(f"🏆 {n_trades}筆交易 — 期望回報最高策略: {best_name}")
        print(f"   期望總回報: +{best_ev:.1f}%")
        print(f"   止盈: {best_strat['tp']*100:.0f}% | 止損: {best_strat['sl']*100:.0f}%")
        print(f"{'='*70}")
    
    # 年化估算
    print(f"\n\n{'='*70}")
    print("📅 年化回報估算（基於 5交易日/筆 ≈ 50筆/年）")
    print(f"{'='*70}")
    print(f"   策略                  30筆中位回報  →  年化估算")
    print(f"   {'-'*55}")
    
    for name, data in all_strat_results.items():
        results = simulate_equity_curve(data['trades'], n_simulations=500, n_trades=30)
        median = sorted([r['total_return'] for r in results])[250]
        # 年化：(1 + median/100)^(50/30) - 1
        annualized = ((1 + median/100) ** (50/30) - 1) * 100
        print(f"   {name:<25} {median:>+7.1f}%  →  {annualized:>+8.1f}%/年")

if __name__ == '__main__':
    main()
