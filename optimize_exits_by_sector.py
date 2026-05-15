#!/usr/bin/env python3
"""
optimize_exits_by_sector.py — 分 Sector 止盈止損參數優化
========================================================
從 backtest_sector_subsector_results.csv 直接計算最優止盈/止損參數。
不重跑完整歷史——用勝率和平均回報推導期望收益。
"""

import pandas as pd
import numpy as np

TAKE_PROFIT_RATES = [0.02, 0.03, 0.04, 0.05, 0.06, 0.08, 0.10, 0.12, 0.15]
STOP_LOSS_RATES   = [0.01, 0.015, 0.02, 0.025, 0.03, 0.04, 0.05]
HOLDING_DAYS = 5

def estimate_win_by_tp_sl(win_rate: float, avg_return: float,
                           tp: float, sl: float) -> dict:
    """
    用擊中概率估算不同止盈/止損下的表現。
    
    假設：
      - 止盈觸發：TP 以內的最高價到達 tp_rate 就算勝
      - 止損觸發：SL 以內的最低價跌破 sl_rate 就算敗
      - 否則：收盤持有至 HOLDING_DAYS，按 avg_return 估算
    """
    wr = max(0.01, min(0.99, win_rate))
    avg_win  =  avg_return / wr          if wr > 0 else 0
    avg_loss = (avg_return / (1 - wr))   if wr < 1 else 0

    # 止盈擊中率估算：avg_return 越大，tp_rate 越小，命中率越高
    tp_hit = min(0.95, max(0.05, (avg_return / tp) * 0.7 + 0.1)) if tp > 0 else 0
    sl_hit = min(0.90, max(0.05, (avg_loss / sl) * 0.5))          if sl > 0 else 0

    # 總勝率 = TP勝 + SL觸發前收盤勝（假設獨立）
    effective_win = min(0.98, tp_hit * win_rate + (1 - tp_hit) * win_rate * 0.6)
    effective_loss_rate = 1 - effective_win

    expected = effective_win * tp - effective_loss_rate * sl
    return {
        'tp':           tp,
        'sl':           sl,
        'win_rate':     effective_win,
        'expected_return': expected,
        'tp_hit_rate':  tp_hit,
    }


def main():
    print("=" * 70)
    print("🎯 分 Sector 止盈止損參數優化（快速版）")
    print("=" * 70)

    try:
        df = pd.read_csv('backtest_sector_subsector_results.csv')
    except FileNotFoundError:
        print("❌ 找不到 backtest_sector_subsector_results.csv")
        return

    # 只看 bullish + 有形態 + 信號數 >= 5
    best = df[
        (df['direction'] == 'bullish') &
        (df['has_pattern'] == True) &
        (df['count'] >= 5) &
        (df['avg_return'] > 0)
    ].copy()

    # 按 sector × signal 聚合，取 count 加權平均
    grouped = best.groupby(['sector', 'signal']).apply(
        lambda g: pd.Series({
            'win_rate':   (g['win_rate']  * g['count']).sum() / g['count'].sum(),
            'avg_return': (g['avg_return'] * g['count']).sum() / g['count'].sum(),
            'count':      g['count'].sum(),
        })
    ).reset_index()

    results = []
    for _, row in grouped.iterrows():
        sector   = row['sector']
        signal   = row['signal']
        wr       = row['win_rate']
        avg_ret  = row['avg_return']
        n        = row['count']

        best_params = None
        best_exp    = -999

        for tp in TAKE_PROFIT_RATES:
            for sl in STOP_LOSS_RATES:
                r = estimate_win_by_tp_sl(wr, avg_ret, tp, sl)
                if r['expected_return'] > best_exp:
                    best_exp = r['expected_return']
                    best_params = r

        if best_params:
            results.append({
                'sector':          sector,
                'signal':          signal,
                'tp':              best_params['tp'],
                'sl':              best_params['sl'],
                'win_rate':        best_params['win_rate'],
                'expected_return': best_params['expected_return'],
                'tp_hit_rate':     best_params['tp_hit_rate'],
                'n_trades':        int(n),
            })

    out = pd.DataFrame(results)
    out = out.sort_values('expected_return', ascending=False)
    out.to_csv('optimal_exits_by_sector.csv', index=False)

    print(f"\n{'='*70}")
    print(f"💾 結果已儲存：optimal_exits_by_sector.csv（共 {len(out)} 個組合）")
    print(f"{'='*70}\n")

    print(f"{'Sector':<30} {'Signal':<22} {'TP':>5} {'SL':>5} "
          f"{'E[ret]':>8} {'勝率':>6} {'止盈擊中':>8} {'樣本':>6}")
    print('─' * 100)
    for _, r in out.iterrows():
        print(f"{str(r['sector'])[:30]:<30} {str(r['signal'])[:22]:<22} "
              f"{r['tp']:>5.0%} {r['sl']:>5.1%} {r['expected_return']:>+7.2%} "
              f"{r['win_rate']:>5.1%} {r['tp_hit_rate']:>7.1%} {r['n_trades']:>6}")

    # ── 按市場通用建議 ──
    print(f"\n{'='*70}")
    print("📋 實務操作建議")
    print(f"{'='*70}")

    for market, sectors in [
        ("🇺🇸 S&P 500", [
            'Information Technology', 'Financials', 'Health Care',
            'Consumer Discretionary', 'Consumer Staples', 'Industrials'
        ]),
        ("🇭🇰 HSI / 香港", [
            'Finance', 'Properties', 'Commerce & Industry', 'Utilities'
        ]),
    ]:
        sub = out[out['sector'].isin(sectors)]
        if not sub.empty:
            avg_tp = sub['tp'].mean()
            avg_sl = sub['sl'].mean()
            avg_exp = sub['expected_return'].mean()
            print(f"\n  {market}")
            print(f"    平均最佳止盈：{avg_tp:.0%} | 平均最佳止損：{avg_sl:.1%}")
            print(f"    平均期望回報：{avg_exp:+.2%}（每筆）")

    # ── 頂層摘要 ──
    top5 = out.head(5)
    print(f"\n{'='*70}")
    print("🏆 Top 5 最佳 (Sector × Signal) 止盈止損組合")
    print(f"{'='*70}")
    for i, (_, r) in enumerate(top5.iterrows(), 1):
        print(f"  {i}. {r['sector']} × {r['signal']}")
        print(f"     TP={r['tp']:.0%} / SL={r['sl']:.1%} | "
              f"E[ret]={r['expected_return']:+.2%} | "
              f"勝率={r['win_rate']:.1%} | {r['n_trades']}筆歷史")


if __name__ == '__main__':
    main()
