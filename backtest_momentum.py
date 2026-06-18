"""
快速回測：驗證新增的動能信號有效性
測試方法：對歷史數據跑 rolling backtest，計算每個信號的 win_rate 和 avg_return
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pathlib import Path
from collections import defaultdict
import pandas as pd
import numpy as np
import duckdb

from indicator_calculator import calculate_all_indicators
from indicator_signals import (
    detect_all_signals,
)
from pattern_detector import (
    detect_all_patterns,
)

# === 參數 ===
FORWARD_DAYS = 5
MIN_SAMPLES = 5
TICKERS = ['MU', 'STX', 'WDC', 'NVDA', 'AMD', 'INTC', 'AVGO', 'QCOM', 'TXN', 
           'MRVL', 'KLAC', 'LRCX', 'AMAT', 'AAPL', 'MSFT', 'GOOGL', 'AMZN', 
           'META', 'TSLA', 'CRM', 'ORCL', 'ADBE']

print(f"📊 回測 {len(TICKERS)} 隻股票，持倉 {FORWARD_DAYS} 天，目標收益 2%")
print("=" * 70)

db_path = Path(__file__).parent / 'data' / 'prices.ddb'
conn = duckdb.connect(str(db_path), read_only=True)

placeholders = ','.join(['?' for _ in TICKERS])
raw_df = pd.read_sql(f"""
    SELECT symbol, trade_date, open, high, low, close, volume
    FROM stock_prices
    WHERE symbol IN ({placeholders})
    ORDER BY symbol, trade_date
""", conn, params=TICKERS)
conn.close()

print(f"  已載入 {len(raw_df)} 行原始數據\n")

# === 分類信號 ===
MOMENTUM_SIGNAL_NAMES = {
    'MACD 金叉 (多頭區)', 'MACD 突破 0 軸',
    'EMA 多頭排列 (20>50>200)', 'KDJ 金叉',
    'RSI 維持強勢 (50-70)', 'RSI 加速上升',
    'RSI 動能加速（強勢區）', 'RSI 上穿 50 中性線',
    '價格創 20 日新高', '價格創 60 日新高',
    '價格在 BB 中軌上方', 'BB 中軌向上（上升趨勢）',
    '成交量配合上升（放量上漲）', '放量突破（強勢確認）', '縮量回調（買點信號）',
    'ADX 強趨勢（多頭主導）', 'ADX 極強趨勢（多頭）',
    '多頭排列 + 價格在均線上方（強勢確認）',
}

REVERSAL_SIGNAL_NAMES = {
    'RSI 超賣區域 (30)', 'RSI 維持超賣',
    'BB 跌破下軌 (超賣)', 'MACD 金叉 (空頭區)',
    'KDJ 超賣區金叉', '價格突破 EMA20',
}

CONTINUATION_PATTERNS = {'Cup & Handle', 'Bull Flag', 'Pullback EMA20', 'Consolidation Breakout'}
REVERSAL_PATTERNS = {'Support', 'Morning Star', 'Bullish Engulfing', 'Bullish Harami'}


def compute_forward_return(df, idx, days=FORWARD_DAYS):
    if idx + days >= len(df):
        return None
    entry = float(df['close'].iloc[idx])
    exit_ = float(df['close'].iloc[idx + days])
    if entry == 0:
        return None
    return (exit_ - entry) / entry


# === 主回測循環 ===
results = defaultdict(lambda: {'wins': 0, 'total': 0, 'returns': []})
combo_results = defaultdict(lambda: {'wins': 0, 'total': 0, 'returns': []})

processed = 0
for sym, grp in raw_df.groupby('symbol'):
    grp = grp.sort_values('trade_date').reset_index(drop=True)
    grp.set_index('trade_date', inplace=True)
    
    if len(grp) < 60:
        continue
    
    ind = calculate_all_indicators(grp.copy())
    
    # 預計算所有形態（一次，不是每個 index 都跑）
    all_patterns = detect_all_patterns(ind)
    # 建立 index → patterns 映射
    patterns_at_idx_map = defaultdict(list)
    for p in all_patterns:
        for i in p.indices:
            patterns_at_idx_map[i].append(p)
    
    for idx in range(30, len(ind) - FORWARD_DAYS - 1):
        sigs = detect_all_signals(ind, idx)
        patterns_at_idx = patterns_at_idx_map.get(idx, [])
        
        fwd_return = compute_forward_return(ind, idx)
        if fwd_return is None:
            continue
        
        is_win = fwd_return >= 0.02
        
        for sig in sigs:
            if sig.signal_type != 'bullish':
                continue
            
            sig_name = sig.name
            is_momentum = sig_name in MOMENTUM_SIGNAL_NAMES
            
            results[sig_name]['total'] += 1
            results[sig_name]['returns'].append(fwd_return)
            if is_win:
                results[sig_name]['wins'] += 1
            
            has_pattern = False
            has_continuation = False
            has_reversal_pattern = False
            for p in patterns_at_idx:
                if p.direction == 'bullish':
                    has_pattern = True
                    if p.name in CONTINUATION_PATTERNS:
                        has_continuation = True
                    if p.name in REVERSAL_PATTERNS:
                        has_reversal_pattern = True
            
            if is_momentum and has_continuation:
                combo_results['⚡動能+延續形態']['total'] += 1
                combo_results['⚡動能+延續形態']['returns'].append(fwd_return)
                if is_win:
                    combo_results['⚡動能+延續形態']['wins'] += 1
            elif is_momentum and has_pattern:
                combo_results['⚡動能+任何形態']['total'] += 1
                combo_results['⚡動能+任何形態']['returns'].append(fwd_return)
                if is_win:
                    combo_results['⚡動能+任何形態']['wins'] += 1
            elif is_momentum:
                combo_results['⚡動能(無形態)']['total'] += 1
                combo_results['⚡動能(無形態)']['returns'].append(fwd_return)
                if is_win:
                    combo_results['⚡動能(無形態)']['wins'] += 1
            
            if sig_name in REVERSAL_SIGNAL_NAMES and has_pattern:
                combo_results['🔄超賣+反轉形態']['total'] += 1
                combo_results['🔄超賣+反轉形態']['returns'].append(fwd_return)
                if is_win:
                    combo_results['🔄超賣+反轉形態']['wins'] += 1
    
    processed += 1
    if processed % 10 == 0:
        print(f"  已處理 {processed}/{len(TICKERS)} 隻股票...")

print(f"\n{'='*70}")
print("📊 回測結果：各信號獨立勝率（5天持倉）")
print(f"{'='*70}")
print(f"{'信號名稱':<30} {'樣本':>6} {'勝率':>8} {'平均回報':>10}")
print('-' * 60)

sorted_sigs = sorted(results.items(), key=lambda x: -x[1]['total'])
for name, data in sorted_sigs:
    if data['total'] < MIN_SAMPLES:
        continue
    wr = data['wins'] / data['total']
    avg_ret = np.mean(data['returns'])
    print(f"{name:<30} {data['total']:>6} {wr:>7.1%} {avg_ret:>+9.2%}")

print(f"\n{'='*70}")
print("📊 混合配搭效果對比")
print(f"{'='*70}")
print(f"{'組合':<25} {'樣本':>6} {'勝率':>8} {'平均回報':>10}")
print('-' * 55)

for name in ['⚡動能+延續形態', '⚡動能+任何形態', '⚡動能(無形態)', '🔄超賣+反轉形態']:
    data = combo_results.get(name)
    if not data or data['total'] < MIN_SAMPLES:
        continue
    wr = data['wins'] / data['total']
    avg_ret = np.mean(data['returns'])
    print(f"{name:<25} {data['total']:>6} {wr:>7.1%} {avg_ret:>+9.2%}")
