#!/usr/bin/env python3
"""
止盈止損優化分析
對每個高勝率組合，掃描最優止盈/止損參數
最大化每筆交易的期望回報
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

# 要分析的最佳組合
TOP_COMBINATIONS = [
    ('BB', 'BB 跌破下軌 (超賣)', 'Support', 'bullish'),
    ('RSI', 'RSI 超賣區域 (30)', 'Support', 'bullish'),
    ('RSI', 'RSI 超賣區域 (30)', 'Morning Star', 'bullish'),
    ('KDJ', 'KDJ 超賣區金叉', 'Support', 'bullish'),
    ('KDJ', 'KDJ J 值極低 (<0)', 'Morning Star', 'bullish'),
    ('BB', 'BB 跌破下軌 (超賣)', 'Bullish Engulfing', 'bullish'),
    ('MACD', 'MACD 金叉 (空頭區)', 'Double Bottom', 'bullish'),
    ('EMA', 'EMA 多頭排列 (20>50>200)', 'Bull Flag', 'bullish'),
    ('EMA', 'EMA 多頭排列 (20>50>200)', 'Double Bottom', 'bullish'),
    ('RSI', 'RSI 超賣區域 (30)', 'Double Bottom', 'bullish'),
]

def load_data(symbols):
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

def collect_trade_paths(df, symbols, combo, holding_days=10):
    """收集特定組合所有信號的完整持有期路徑"""
    indicator_name, signal_name, pattern_name, direction = combo
    
    all_paths = []
    
    for sym in symbols:
        sym_df = df[df['symbol'] == sym].copy().reset_index(drop=True)
        if len(sym_df) < 60:
            continue
        
        try:
            ind_df = calculate_all_indicators(sym_df)
            patterns = detect_all_patterns(sym_df)
        except:
            continue
        
        # pattern dict
        pat_dict = defaultdict(list)
        for p in patterns:
            for idx in p.indices:
                for lookback in range(max(0, idx - 3), min(len(sym_df), idx + 4)):
                    pat_dict[lookback].append(p)
        
        # scan signals
        for sig_idx in range(20, len(ind_df) - holding_days):
            signals = detect_all_signals(ind_df, sig_idx)
            signals = [s for s in signals 
                      if s.indicator == indicator_name 
                      and s.name == signal_name
                      and s.signal_type == direction]
            if not signals:
                continue
            
            sig = signals[0]
            
            # check pattern
            nearby = pat_dict.get(sig_idx, [])
            matching = [p for p in nearby 
                       if p.direction == 'bullish' and p.name == pattern_name]
            if not matching:
                continue
            
            # 計算持有期內每天的回報（相對於進場價）
            entry_price = sym_df['close'].iloc[sig_idx]
            path = []
            for d in range(1, holding_days + 1):
                future_close = sym_df['close'].iloc[min(sig_idx + d, len(sym_df) - 1)]
                ret = (future_close / entry_price) - 1
                path.append(ret)
            
            # 也計算最高/最低價
            high_path = []
            low_path = []
            for d in range(1, holding_days + 1):
                window_high = sym_df['high'].iloc[sig_idx:sig_idx + d + 1].max()
                window_low = sym_df['low'].iloc[sig_idx:sig_idx + d + 1].min()
                high_path.append((window_high / entry_price) - 1)
                low_path.append((window_low / entry_price) - 1)
            
            all_paths.append({
                'symbol': sym,
                'entry_idx': sig_idx,
                'path_close': path,
                'path_high': high_path,
                'path_low': low_path,
            })
    
    return all_paths

def optimize_stoploss_takeprofit(paths, tp_levels, sl_levels, holding_days=5):
    """
    掃描最優止盈止損組合
    對每個 (tp, sl) 計算期望回報
    paths: 每個信號的持有期回報路徑（相對於進場價）
    """
    results = []
    
    for tp in tp_levels:
        for sl in sl_levels:
            returns = []
            win_count = 0
            loss_count = 0
            
            for p in paths:
                # 模擬交易：止盈或止損先觸發就離場
                triggered = False
                exit_ret = 0.0
                
                for d in range(1, min(holding_days, len(p['path_close'])) + 1):
                    high_ret = p['path_high'][d - 1]   # 當日最高價回報
                    low_ret = p['path_low'][d - 1]     # 當日最低價回報
                    
                    if high_ret >= tp:
                        # 止盈觸發（當日最高價觸及）
                        exit_ret = tp
                        triggered = True
                        break
                    elif low_ret <= -sl:
                        # 止損觸發（當日最低價觸及）
                        exit_ret = -sl
                        triggered = True
                        break
                
                if not triggered:
                    # 都沒觸發，持有到第 holding_days 天收盤
                    exit_ret = p['path_close'][holding_days - 1]
                
                returns.append(exit_ret)
                if exit_ret > 0:
                    win_count += 1
                else:
                    loss_count += 1
            
            cnt = len(returns)
            if cnt < 15:
                continue
            
            wins = sum(1 for r in returns if r > 0)
            losses = cnt - wins
            avg_win = sum(r for r in returns if r > 0) / wins if wins > 0 else 0
            avg_loss = sum(r for r in returns if r <= 0) / losses if losses > 0 else 0
            
            win_rate = wins / cnt
            avg_return = sum(returns) / cnt
            expect_value = win_rate * avg_win + (1 - win_rate) * avg_loss
            
            # 凱利因子
            if avg_loss != 0:
                b = avg_win / abs(avg_loss)
                if b > 0:
                    kelly = (b * win_rate - (1 - win_rate)) / b
                    kelly = max(0, min(1, kelly))
                else:
                    kelly = 0
            else:
                kelly = 0
            
            results.append({
                'tp': tp, 'sl': sl,
                'count': cnt, 'wins': wins, 'losses': losses,
                'win_rate': win_rate,
                'avg_return': avg_return,
                'avg_win': avg_win,
                'avg_loss': avg_loss,
                'expect_value': expect_value,
                'kelly': kelly,
            })
    
    return pd.DataFrame(results)

def analyze_combination(df, symbols, combo, market):
    indicator, signal, pattern, direction = combo
    label = f"{indicator} {signal} + {pattern}"
    print(f"\n{'='*70}")
    print(f"📊 {label} ({market})")
    print(f"{'='*70}")
    
    paths = collect_trade_paths(df, symbols, combo, holding_days=10)
    print(f"   收集到 {len(paths)} 個信號")
    
    if len(paths) < 15:
        print(f"   樣本不足，跳過")
        return None
    
    # 基礎統計
    final_rets = [p['path_close'][4] for p in paths]  # 5日收盤回報
    peak_rets = [max(p['path_high']) for p in paths]  # 持有期內峰值
    max_downs = [min(p['path_low']) for p in paths]   # 持有期內最大回撤
    
    print(f"\n   持有5天後（不做止盈止損）：")
    print(f"   平均回報: {np.mean(final_rets)*100:+.2f}%")
    print(f"   勝率(>0%): {sum(1 for r in final_rets if r>0)/len(final_rets)*100:.1f}%")
    print(f"   勝率(>2%): {sum(1 for r in final_rets if r>0.02)/len(final_rets)*100:.1f}%")
    print(f"   峰值回報(平均): {np.mean(peak_rets)*100:+.2f}%")
    print(f"   最大回撤(平均): {np.mean(max_downs)*100:+.2f}%")
    
    # 止盈止損優化
    tp_levels = [0.02, 0.03, 0.04, 0.05, 0.06, 0.08, 0.10, 0.12, 0.15]
    sl_levels = [0.01, 0.015, 0.02, 0.025, 0.03, 0.04, 0.05]
    
    opt_df = optimize_stoploss_takeprofit(paths, tp_levels, sl_levels, holding_days=5)
    
    if len(opt_df) == 0:
        return None
    
    # 最優期望值
    best_ev = opt_df.loc[opt_df['expect_value'].idxmax()]
    # 最優勝率
    best_wr = opt_df.loc[opt_df['win_rate'].idxmax()]
    
    print(f"\n   🏆 最優期望值策略:")
    print(f"   止盈: {best_ev['tp']*100:.0f}%  止損: {best_ev['sl']*100:.1f}%")
    print(f"   期望回報: {best_ev['expect_value']*100:+.2f}%")
    print(f"   勝率: {best_ev['win_rate']*100:.1f}%  (W:{best_ev['wins']} L:{best_ev['losses']})")
    print(f"   平均贏: {best_ev['avg_win']*100:+.2f}%  平均虧: {best_ev['avg_loss']*100:+.2f}%")
    print(f"   理論凱利倉位: {best_ev['kelly']*100:.0f}%  半凱利: {best_ev['kelly']*50:.0f}%")
    
    print(f"\n   🏆 最高勝率策略:")
    print(f"   止盈: {best_wr['tp']*100:.0f}%  止損: {best_wr['sl']*100:.1f}%")
    print(f"   勝率: {best_wr['win_rate']*100:.1f}%  (W:{best_wr['wins']} L:{best_wr['losses']})")
    print(f"   期望回報: {best_wr['expect_value']*100:+.2f}%")
    
    # 顯示完整熱力圖 top 組合
    top_combos = opt_df.nlargest(10, 'expect_value')
    print(f"\n   📋 Top 10 期望值參數組合:")
    print(f"   {'止盈':>6} {'止損':>6} {'勝率':>7} {'期望值':>8} {'平均贏':>8} {'平均虧':>8} {'凱利':>6}")
    print(f"   {'-'*55}")
    for _, r in top_combos.iterrows():
        print(f"   {r['tp']*100:>5.0f}% {r['sl']*100:>5.1f}% {r['win_rate']*100:>6.1f}% {r['expect_value']*100:>+7.2f}% {r['avg_win']*100:>+7.2f}% {r['avg_loss']*100:>+7.2f}% {r['kelly']*100:>5.0f}%")
    
    return {
        'combo': label, 'market': market, 'sample': len(paths),
        'best_ev_tp': best_ev['tp'], 'best_ev_sl': best_ev['sl'],
        'best_ev_ev': best_ev['expect_value'], 'best_ev_wr': best_ev['win_rate'],
        'best_ev_avg_win': best_ev['avg_win'], 'best_ev_avg_loss': best_ev['avg_loss'],
        'best_ev_kelly': best_ev['kelly'],
        'best_wr_tp': best_wr['tp'], 'best_wr_sl': best_wr['sl'],
        'best_wr_wr': best_wr['win_rate'], 'best_wr_ev': best_wr['expect_value'],
        'no_tp_wr': sum(1 for r in final_rets if r>0)/len(final_rets),
        'no_tp_ev': np.mean(final_rets),
    }

def main():
    sp500 = [s.strip() for s in open('data/constituents_sp500.txt') if s.strip()]
    hsi = [s.strip() for s in open('data/constituents_hsi.txt') if s.strip()]
    
    df = load_data(sp500 + hsi)
    
    print("=" * 70)
    print("🎯 止盈止損優化分析 — 最大化每筆交易回報")
    print("   持有期: 5個交易日 | 止盈: 2%-15% | 止損: 1%-5%")
    print("=" * 70)
    
    all_results = []
    
    # S&P 500 分析
    print(f"\n\n{'#'*70}")
    print("S&P 500 市場")
    print(f"{'#'*70}")
    
    for combo in TOP_COMBINATIONS:
        r = analyze_combination(df, sp500, combo, 'S&P 500')
        if r:
            all_results.append(r)
    
    # HSI 分析  
    hsi_combos = [
        ('BB', 'BB 跌破下軌 (超賣)', 'Support', 'bullish'),
        ('RSI', 'RSI 超賣區域 (30)', 'Support', 'bullish'),
        ('RSI', 'RSI 超賣區域 (30)', 'Morning Star', 'bullish'),
        ('KDJ', 'KDJ 超賣區金叉', 'Support', 'bullish'),
        ('KDJ', 'KDJ J 值極低 (<0)', 'Morning Star', 'bullish'),
        ('BB', 'BB 跌破下軌 (超賣)', 'Double Bottom', 'bullish'),
        ('MACD', 'MACD 金叉 (空頭區)', 'Support', 'bullish'),
        ('MACD', 'MACD 金叉 (空頭區)', 'Double Bottom', 'bullish'),
    ]
    
    print(f"\n\n{'#'*70}")
    print("HSI 市場")
    print(f"{'#'*70}")
    
    for combo in hsi_combos:
        r = analyze_combination(df, hsi, combo, 'HSI')
        if r:
            all_results.append(r)
    
    # 總結表
    print(f"\n\n{'='*70}")
    print("📊 所有組合最佳止盈止損策略總結")
    print(f"{'='*70}")
    print(f"\n{'組合':<40} {'市場':<8} {'樣本':>5} {'止盈':>5} {'止損':>5} {'期望':>7} {'勝率':>6} {'凱利':>6}")
    print(f"{'-'*85}")
    
    for r in sorted(all_results, key=lambda x: -x['best_ev_ev']):
        combo_short = r['combo'].replace(' 超賣區域 (30)', '').replace(' 跌破下軌 (超賣)', '↓').replace(' 超賣區金叉', '金叉').replace(' 多頭排列 (20>50>200)', '多頭')
        tp_pct = int(r['best_ev_tp'] * 100)
        sl_pct = int(r['best_ev_sl'] * 100)
        ev_pct = r['best_ev_ev'] * 100
        wr_pct = r['best_ev_wr'] * 100
        kelly_pct = r['best_ev_kelly'] * 100
        print(f"{combo_short:<40} {r['market']:<8} {r['sample']:>5} {tp_pct:>4}% {sl_pct:>4}% {ev_pct:>+6.2f}% {wr_pct:>5.1f}% {kelly_pct:>5.0f}%")
    
    # 保存
    summary_df = pd.DataFrame(all_results)
    summary_df.to_csv('optimal_exits_summary.csv', index=False)
    print(f"\n💾 已保存至 optimal_exits_summary.csv")

if __name__ == '__main__':
    main()
