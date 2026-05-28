#!/usr/bin/env python3
"""只回測 TRMB 一檔股票，快速回答 Sector/Stock 勝率"""
import sys, os, warnings
warnings.filterwarnings('ignore')
from collections import defaultdict
import duckdb
import pandas as pd
from io import StringIO
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from indicator_calculator import calculate_all_indicators
from indicator_signals import detect_rsi_signals, detect_macd_signals, detect_kdj_signals, detect_ema_signals, detect_bb_signals
from pattern_detector import Pattern, detect_doji, detect_hammer, detect_shooting_star, detect_morning_star, detect_evening_star, detect_engulfing, detect_harami, detect_support_resistance, detect_flag, detect_triangle

FORWARD_DAYS = 5; THRESHOLD = 0.02; MIN_SIGNALS = 5; MIN_PATTERN_CONFIDENCE = 0.5
VOL_MA_PERIOD = 20; VOL_SPIKE_TODAY = 1.5; VOL_SPIKE_NEXT = 1.2

BULLISH_INDICATORS = {
    ('RSI', 'RSI 超賣區域 (30)'), ('RSI', 'RSI 維持超賣'), ('BB', 'BB 跌破下軌 (超賣)'),
    ('MACD', 'MACD 金叉 (空頭區)'), ('MACD', 'MACD 突破 0 軸'), ('KDJ', 'KDJ 超賣區金叉'),
    ('EMA', 'EMA 黃金交叉 (20 上穿 50)'), ('EMA', '價格突破 EMA20'),
}
BEARISH_INDICATORS = {
    ('RSI', 'RSI 超買區域 (70)'), ('RSI', 'RSI 維持超買'), ('BB', 'BB 突破上軌 (超買)'),
    ('MACD', 'MACD 死叉 (空頭區)'), ('MACD', 'MACD 跌破 0 軸'), ('KDJ', 'KDJ 超買區死叉'),
    ('EMA', 'EMA 死亡交叉 (20 下穿 50)'), ('EMA', '價格跌破 EMA20'),
}
BULLISH_PATTERNS = {'Support', 'Morning Star', 'Bullish Engulfing', 'Bull Flag', 'Hammer', 'Ascending Triangle', 'Bullish Harami'}
BEARISH_PATTERNS = {'Resistance', 'Evening Star', 'Bearish Engulfing', 'Bear Flag', 'Shooting Star', 'Descending Triangle', 'Bearish Harami'}

def fetch_sp500_sectors():
    h = {'User-Agent': 'Mozilla/5.0'}
    r = requests.get('https://en.wikipedia.org/wiki/List_of_S%26P_500_companies', headers=h, timeout=15)
    df = pd.read_html(StringIO(r.text))[0][['Symbol', 'GICS Sector']].rename(columns={'Symbol': 'ticker', 'GICS Sector': 'sector'})
    df['ticker'] = df['ticker'].str.strip()
    return df

class PatternIndex:
    def __init__(self, p): self.pattern = p
    def covers(self, i): return i in self.pattern.indices

def build_pattern_index(df):
    df = df.copy()
    df['vol_ma20'] = df['volume'].rolling(VOL_MA_PERIOD, min_periods=10).mean()
    bull_idx = defaultdict(list); bear_idx = defaultdict(list)
    for i in range(5, len(df)):
        for det in [lambda i: detect_doji(df,i), lambda i: detect_hammer(df,i), lambda i: detect_shooting_star(df,i),
                    lambda i: detect_morning_star(df,i), lambda i: detect_evening_star(df,i), lambda i: detect_engulfing(df,i),
                    lambda i: detect_harami(df,i)]:
            try:
                p = det(i)
                if p and p.confidence >= MIN_PATTERN_CONFIDENCE:
                    pi = PatternIndex(p)
                    for j in p.indices:
                        if p.direction == 'bullish' and p.name in BULLISH_PATTERNS: bull_idx[j].append(pi)
                        elif p.direction == 'bearish' and p.name in BEARISH_PATTERNS: bear_idx[j].append(pi)
            except: pass
        for det in [detect_support_resistance, detect_flag, detect_triangle]:
            try:
                for p in det(df):
                    if p.confidence < MIN_PATTERN_CONFIDENCE: continue
                    pi = PatternIndex(p)
                    for j in p.indices:
                        if p.direction == 'bullish' and p.name in BULLISH_PATTERNS: bull_idx[j].append(pi)
                        elif p.direction == 'bearish' and p.name in BEARISH_PATTERNS: bear_idx[j].append(pi)
            except: pass
    return bull_idx, bear_idx, df

def load_data(sym, start=None):
    conn = duckdb.connect('data/prices.ddb', read_only=True)
    if start:
        df = pd.read_sql_query("SELECT trade_date as date,symbol,open,high,low,close,volume FROM stock_prices WHERE symbol=? AND trade_date>=? ORDER BY trade_date ASC", conn, params=(sym,start))
    else:
        df = pd.read_sql_query("SELECT trade_date as date,symbol,open,high,low,close,volume FROM stock_prices WHERE symbol=? ORDER BY trade_date ASC", conn, params=(sym,))
    conn.close()
    if df.empty: return df
    df['date'] = pd.to_datetime(df['date'])
    return df

def backtest_one(sym, sector, start=None):
    df = load_data(sym, start)
    if df.empty or len(df) < 60: return []
    df = df.reset_index(drop=True)
    df = calculate_all_indicators(df)
    bull_idx, bear_idx, df = build_pattern_index(df)
    trades = []
    for i in range(30, len(df)):
        vol_today_ok = vol_next_ok = False
        if i+1 < len(df):
            vt = df['volume'].iloc[i]; vm = df['vol_ma20'].iloc[i]
            vn = df['volume'].iloc[i+1]; vmn = df['vol_ma20'].iloc[i+1]
            if vm > 0: vol_today_ok = vt >= vm * VOL_SPIKE_TODAY
            if vmn > 0: vol_next_ok = vn >= vmn * VOL_SPIKE_NEXT
        vol_conf = vol_today_ok and vol_next_ok
        all_sigs = []
        for f in [detect_rsi_signals, detect_macd_signals, detect_kdj_signals, detect_ema_signals, detect_bb_signals]:
            all_sigs.extend(f(df, i))
        bull_pis = bull_idx.get(i, []); bear_pis = bear_idx.get(i, [])
        for sig in all_sigs:
            k = (sig.indicator, sig.name)
            is_bull = k in BULLISH_INDICATORS and sig.signal_type == 'bullish'
            is_bear = k in BEARISH_INDICATORS and sig.signal_type == 'bearish'
            if not (is_bull or is_bear): continue
            matched_pat = None; matched_conf = 0.0
            if sig.signal_type == 'bullish':
                for pi in bull_pis:
                    if pi.pattern.confidence > matched_conf:
                        matched_pat = pi.pattern.name; matched_conf = pi.pattern.confidence
            else:
                for pi in bear_pis:
                    if pi.pattern.confidence > matched_conf:
                        matched_pat = pi.pattern.name; matched_conf = pi.pattern.confidence
            has_pat = matched_pat is not None
            if i + FORWARD_DAYS >= len(df): continue
            entry = df['close'].iloc[i]; exitp = df['close'].iloc[i+FORWARD_DAYS]
            ret = (exitp - entry) / entry
            if sig.signal_type == 'bearish': ret = -ret
            trades.append({'symbol': sym, 'sector': sector, 'signal': sig.name,
                           'matched_pattern': matched_pat or 'None', 'has_pattern': has_pat,
                           'volume_confirmed': vol_conf, 'return': ret, 'is_success': ret > THRESHOLD})
    return trades

def run_for_period(name, start=None):
    sector_map = dict(zip(fetch_sp500_sectors()['ticker'], fetch_sp500_sectors()['sector']))
    sector = sector_map.get('TRMB', 'Information Technology')
    trades = backtest_one('TRMB', sector, start)
    print(f"\n{name}: {len(trades)} 個信號")
    if not trades: return None, None
    stock_agg = defaultdict(lambda: {'count':0,'successes':0,'total_return':0.0})
    sector_agg = defaultdict(lambda: {'count':0,'successes':0,'total_return':0.0})
    for t in trades:
        sk = (t['symbol'], t['sector'], t['signal'], t['matched_pattern'], t['has_pattern'], t['volume_confirmed'])
        sk2 = (t['sector'], t['signal'], t['matched_pattern'], t['has_pattern'], t['volume_confirmed'])
        for agg, key in [(stock_agg, sk), (sector_agg, sk2)]:
            agg[key]['count'] += 1; agg[key]['total_return'] += t['return']
            if t['is_success']: agg[key]['successes'] += 1
    stock_df = pd.DataFrame([{**{'symbol':k[0],'sector':k[1],'signal':k[2],'matched_pattern':k[3],'has_pattern':k[4],'volume_confirmed':k[5],
                                  'count':v['count'],'win_rate':v['successes']/v['count'],'avg_return':v['total_return']/v['count']}}
                              for k,v in stock_agg.items() if v['count']>=MIN_SIGNALS])
    sector_df = pd.DataFrame([{**{'sector':k[0],'signal':k[1],'matched_pattern':k[2],'has_pattern':k[3],'volume_confirmed':k[4],
                                  'count':v['count'],'win_rate':v['successes']/v['count'],'avg_return':v['total_return']/v['count']}}
                              for k,v in sector_agg.items() if v['count']>=MIN_SIGNALS])
    return stock_df, sector_df

def fmt(stk):
    if stk is None or stk.empty: return '無數據'
    r = stk.iloc[0]
    return f"win={r['win_rate']:.1%} avg={r['avg_return']:+.2f}% n={r['count']}"

def lookup(name, stk_df, sec_df):
    sig='KDJ 超賣區金叉'; pat='Support'; vol=False; has_pat=True
    stk = stk_df[(stk_df['symbol']=='TRMB')&(stk_df['signal']==sig)&(stk_df['matched_pattern']==pat)&(stk_df['has_pattern']==has_pat)&(stk_df['volume_confirmed']==vol)] if stk_df is not None else None
    sec = sec_df[(sec_df['sector']=='Information Technology')&(sec_df['signal']==sig)&(sec_df['matched_pattern']==pat)&(sec_df['has_pattern']==has_pat)&(sec_df['volume_confirmed']==vol)] if sec_df is not None else None
    fb = sec_df[(sec_df['signal']==sig)&(sec_df['matched_pattern']==pat)&(sec_df['has_pattern']==has_pat)&(sec_df['volume_confirmed']==vol)] if sec_df is not None else None
    print(f"\n{name}")
    print(f"  Stock (TRMB):       {fmt(stk)}")
    print(f"  Sector (IT):        {fmt(sec)}")
    print(f"  Fallback (all sec): {fmt(fb)}")

if __name__ == '__main__':
    # TRMB 1年數據（2025-05-26 起）
    stk1, sec1 = run_for_period("【1年數據 2025-05-26起】", start='2025-05-26')
    # TRMB 全量數據
    stkA, secA = run_for_period("【全量數據 2021-10-07起】")
    print("\n" + "="*55)
    print("TRMB | KDJ 超賣區金叉 | Support")
    print("="*55)
    lookup("【1年】", stk1, sec1)
    lookup("【全量】", stkA, secA)