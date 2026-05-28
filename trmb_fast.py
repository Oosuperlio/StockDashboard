#!/usr/bin/env python3
"""TRMB backtest: compare 1yr vs full data for KDJ超賣區金叉 + Support"""
import sys, os, warnings; warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collections import defaultdict
import duckdb, pandas as pd
from indicator_calculator import calculate_all_indicators
from indicator_signals import detect_rsi_signals, detect_macd_signals, detect_kdj_signals, detect_ema_signals, detect_bb_signals
from pattern_detector import Pattern, detect_doji, detect_hammer, detect_shooting_star, detect_morning_star, detect_evening_star, detect_engulfing, detect_harami, detect_support_resistance, detect_flag, detect_triangle

FWD=5; THR=0.02; MIN_PC=0.5
VOL_MA=20; VS=1.5; VSN=1.2
BI_PATS={'Support','Morning Star','Bullish Engulfing','Bull Flag','Hammer','Ascending Triangle','Bullish Harami'}
BEAR_PATS={'Resistance','Evening Star','Bearish Engulfing','Bear Flag','Shooting Star','Descending Triangle','Bearish Harami'}
BULL_IND={('RSI','RSI 超賣區域 (30)'),('RSI','RSI 維持超賣'),('BB','BB 跌破下軌 (超賣)'),('MACD','MACD 金叉 (空頭區)'),('MACD','MACD 突破 0 軸'),('KDJ','KDJ 超賣區金叉'),('EMA','EMA 黃金交叉 (20 上穿 50)'),('EMA','價格突破 EMA20')}
BEAR_IND={('RSI','RSI 超買區域 (70)'),('RSI','RSI 維持超買'),('BB','BB 突破上軌 (超買)'),('MACD','MACD 死叉 (空頭區)'),('MACD','MACD 跌破 0 軸'),('KDJ','KDJ 超買區死叉'),('EMA','EMA 死亡交叉 (20 下穿 50)'),('EMA','價格跌破 EMA20')}

class PIdx:
    def __init__(self, p): self.pattern = p
    def covers(self, i): return i in self.pattern.indices

def build_idx(df):
    df = df.copy()
    df['vol_ma20'] = df['volume'].rolling(VOL_MA, min_periods=10).mean()
    bi, bari = defaultdict(list), defaultdict(list)
    for i in range(5, len(df)):
        for det in [lambda i: detect_doji(df,i), lambda i: detect_hammer(df,i), lambda i: detect_shooting_star(df,i), lambda i: detect_morning_star(df,i), lambda i: detect_evening_star(df,i), lambda i: detect_engulfing(df,i), lambda i: detect_harami(df,i)]:
            try:
                p = det(i)
                if p and p.confidence >= MIN_PC:
                    pi = PIdx(p)
                    for j in p.indices:
                        if p.direction=='bullish' and p.name in BI_PATS: bi[j].append(pi)
                        elif p.direction=='bearish' and p.name in BEAR_PATS: bari[j].append(pi)
            except: pass
        for det in [detect_support_resistance, detect_flag, detect_triangle]:
            try:
                for p in det(df):
                    if p.confidence < MIN_PC: continue
                    pi = PIdx(p)
                    for j in p.indices:
                        if p.direction=='bullish' and p.name in BI_PATS: bi[j].append(pi)
                        elif p.direction=='bearish' and p.name in BEAR_PATS: bari[j].append(pi)
            except: pass
    return bi, bari, df

def load(sym, start=None):
    conn = duckdb.connect('data/prices.ddb', read_only=True)
    if start:
        df = pd.read_sql_query("SELECT trade_date as date,open,high,low,close,volume FROM stock_prices WHERE symbol=? AND trade_date>=? ORDER BY trade_date", conn, params=(sym,start))
    else:
        df = pd.read_sql_query("SELECT trade_date as date,open,high,low,close,volume FROM stock_prices WHERE symbol=? ORDER BY trade_date", conn, params=(sym,))
    conn.close()
    df['date'] = pd.to_datetime(df['date'])
    return df

def run(name, start=None):
    print(f"\n[{name}] Loading data...", end=" ", flush=True)
    df = load('TRMB', start)
    if df.empty or len(df) < 30:
        print("Insufficient data")
        return {}, {}
    print(f"rows={len(df)}")
    df = df.reset_index(drop=True)
    df = calculate_all_indicators(df)
    bi, bari, df = build_idx(df)
    print(f"  Patterns indexed. Starting scan...")
    stock_agg = defaultdict(lambda:{'count':0,'successes':0,'total_return':0.0})
    sector_agg = defaultdict(lambda:{'count':0,'successes':0,'total_return':0.0})
    for i in range(20, len(df)):
        vt = df['volume'].iloc[i]; vm = df['vol_ma20'].iloc[i]
        vn = df['volume'].iloc[i+1] if i+1 < len(df) else 0
        vmn = df['vol_ma20'].iloc[i+1] if i+1 < len(df) else 0
        vol_today_ok = vm>0 and vt>=vm*VS
        vol_next_ok = vmn>0 and vn>=vmn*VSN
        vol_conf = vol_today_ok and vol_next_ok
        all_sigs = []
        for f in [detect_rsi_signals, detect_macd_signals, detect_kdj_signals, detect_ema_signals, detect_bb_signals]:
            all_sigs.extend(f(df, i))
        bull_pis = bi.get(i, []); bear_pis = bari.get(i, [])
        for sig in all_sigs:
            k = (sig.indicator, sig.name)
            is_bull = k in BULL_IND and sig.signal_type=='bullish'
            is_bear = k in BEAR_IND and sig.signal_type=='bearish'
            if not (is_bull or is_bear): continue
            matched_pat = None; conf = 0.0
            if sig.signal_type=='bullish':
                for pi in bull_pis:
                    if pi.pattern.confidence > conf:
                        matched_pat = pi.pattern.name; conf = pi.pattern.confidence
            else:
                for pi in bear_pis:
                    if pi.pattern.confidence > conf:
                        matched_pat = pi.pattern.name; conf = pi.pattern.confidence
            has_pat = matched_pat is not None
            if i+FWD >= len(df): continue
            entry = df['close'].iloc[i]; exitp = df['close'].iloc[i+FWD]
            ret = (exitp-entry)/entry
            if sig.signal_type=='bearish': ret = -ret
            is_suc = ret > THR
            sk_stk = ('TRMB','Information Technology', sig.name, matched_pat or 'None', has_pat, vol_conf)
            sk_sec = ('Information Technology', sig.name, matched_pat or 'None', has_pat, vol_conf)
            for agg, key in [(stock_agg, sk_stk), (sector_agg, sk_sec)]:
                agg[key]['count'] += 1; agg[key]['total_return'] += ret
                if is_suc: agg[key]['successes'] += 1
    def to_df(d):
        if not d: return pd.DataFrame()
        rows = []
        for k,v in d.items():
            if v['count'] < 3: continue
            rows.append({'key':k,'count':v['count'],'win_rate':v['successes']/v['count'],'avg_return':v['total_return']/v['count']})
        return pd.DataFrame(rows)
    return to_df(stock_agg), to_df(sector_agg)

def fmt(df2):
    if df2 is None or len(df2)==0: return '無數據 (n<3)'
    r = df2.iloc[0]
    return f"win={r['win_rate']:.1%} avg={r['avg_return']:+.2f}% n={r['count']}"

def lookup(name, stk_df, sec_df):
    sig='KDJ 超賣區金叉'; pat='Support'; vol=False; hp=True
    sk = ('TRMB','Information Technology',sig,pat,hp,vol)
    sek = ('Information Technology',sig,pat,hp,vol)
    stk = stk_df[stk_df['key'].apply(lambda x: x==sk)] if stk_df is not None and len(stk_df)>0 else pd.DataFrame()
    sec = sec_df[sec_df['key'].apply(lambda x: x==sek)] if sec_df is not None and len(sec_df)>0 else pd.DataFrame()
    fb = sec_df[(sec_df['key'].apply(lambda x: x[1]==sig and x[2]==pat and x[4]==hp and x[5]==vol))] if sec_df is not None and len(sec_df)>0 else pd.DataFrame()
    print(f"\n{name}")
    print(f"  Stock (TRMB):       {fmt(stk)}")
    print(f"  Sector (IT):        {fmt(sec)}")
    print(f"  Fallback (all sectors): {fmt(fb)}")

if __name__ == '__main__':
    print("="*55)
    print("TRMB | KDJ 超賣區金叉 | Support")
    print("="*55)
    stk1, sec1 = run("1年數據 (2025-05-26起)", '2025-05-26')
    stkF, secF = run("全量數據 (2021-10-07起)")
    lookup("【1年】", stk1, sec1)
    lookup("【全量】", stkF, secF)