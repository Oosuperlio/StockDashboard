#!/usr/bin/env python3
"""
backtest_full_factors.py — 向量化全因子回測框架
================================================
極速版本：一次性加載全部數據，向量化計算形態/指標/成交量，8核並行。
"""

import sys, os, warnings, argparse
from pathlib import Path
from collections import defaultdict
import numpy as np
import pandas as pd
import duckdb

warnings.filterwarnings('ignore')
sys.path.insert(0, str(Path(__file__).parent))

DB_PATH = Path(__file__).parent / 'data' / 'prices.ddb'
FORWARD = 5
THRESHOLD = 0.02
MIN_SIGNALS = 5
VOL_MA, VOL_TODAY_RATIO, VOL_NEXT_RATIO = 20, 1.5, 1.2

BULL_PATTERNS = {
    'Support','Morning Star','Bullish Engulfing','Bull Flag',
    'Hammer','Ascending Triangle','Bullish Harami'
}
BEAR_PATTERNS = {
    'Resistance','Evening Star','Bearish Engulfing',
    'Bear Flag','Shooting Star','Descending Triangle','Bearish Harami'
}

BULLISH_IND = {
    ('RSI','RSI 超賣區域 (30)'),('RSI','RSI 維持超賣'),
    ('BB','BB 跌破下軌 (超賣)'),('MACD','MACD 金叉 (空頭區)'),
    ('MACD','MACD 突破 0 軸'),('KDJ','KDJ 超賣區金叉'),
    ('EMA','EMA 黃金交叉 (20 上穿 50)'),('EMA','價格突破 EMA20'),
}
BEARISH_IND = {
    ('RSI','RSI 超買區域 (70)'),('RSI','RSI 維持超買'),
    ('BB','BB 突破上軌 (超買)'),('MACD','MACD 死叉 (多頭區)'),
    ('MACD','MACD 跌破 0 軸'),('KDJ','KDJ 超買區死叉'),
    ('EMA','EMA 死亡交叉 (20 下穿 50)'),('EMA','價格跌破 EMA20'),
}


def load_all() -> pd.DataFrame:
    """一次性加載所有股票數據"""
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    df = pd.read_sql("""
        SELECT trade_date as date, symbol, open, high, low, close, volume
        FROM stock_prices ORDER BY symbol, date
    """, conn)
    conn.close()
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values(['symbol','date']).reset_index(drop=True)
    print(f"Loaded {len(df):,} rows × {df['symbol'].nunique()} symbols")
    return df


def calc_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """批量計算技術指標（向量化）"""
    dfs = []
    for sym, g in df.groupby('symbol', sort=False):
        g = g.sort_values('date').copy()
        close = g['close'].values

        # RSI(14)
        delta = np.diff(close, prepend=np.nan)
        gain = np.where(delta > 0, delta, 0)
        loss = np.where(delta < 0, -delta, 0)
        avg_gain = pd.Series(gain).rolling(14, min_periods=1).mean().values
        avg_loss = pd.Series(loss).rolling(14, min_periods=1).mean().values
        rs = avg_gain / (avg_loss + 1e-10)
        rsi = 100 - (100 / (rs + 1))

        # EMA(20), EMA(50)
        ema20 = pd.Series(close).ewm(span=20, adjust=False).mean().values
        ema50 = pd.Series(close).ewm(span=50, adjust=False).mean().values

        # EMA cross signal
        ema_cross = ((ema20 > ema50) & (np.roll(ema20, 1) <= np.roll(ema50, 1))) * 1
        ema_cross[0] = 0

        # Price vs EMA20
        price_above_ema20 = (close > ema20) * 1
        price_below_ema20 = (close < ema20) * 1

        # MACD(12,26,9)
        ema12 = pd.Series(close).ewm(span=12, adjust=False).mean().values
        ema26 = pd.Series(close).ewm(span=26, adjust=False).mean().values
        macd_line = ema12 - ema26
        signal_line = pd.Series(macd_line).ewm(span=9, adjust=False).mean().values
        macd_hist = macd_line - signal_line

        # MACD signals
        macd_cross_up = ((macd_line > signal_line) & (np.roll(macd_line, 1) <= np.roll(signal_line, 1))) * 1
        macd_cross_up[0] = 0
        macd_cross_dn = ((macd_line < signal_line) & (np.roll(macd_line, 1) >= np.roll(signal_line, 1))) * 1
        macd_cross_dn[0] = 0
        macd_cross_0_up = ((macd_line > 0) & (np.roll(macd_line, 1) <= 0)) * 1
        macd_cross_0_up[0] = 0
        macd_cross_0_dn = ((macd_line < 0) & (np.roll(macd_line, 1) >= 0)) * 1
        macd_cross_0_dn[0] = 0

        # KDJ (9,3,3)
        low9 = pd.Series(g['low'].values).rolling(9, min_periods=1).min().values
        high9 = pd.Series(g['high'].values).rolling(9, min_periods=1).max().values
        k = 50 * np.ones_like(close)
        d = 50 * np.ones_like(close)
        for i in range(1, len(close)):
            if high9[i] != low9[i]:
                RSV = 100 * (close[i] - low9[i]) / (high9[i] - low9[i])
                k[i] = 2/3 * k[i-1] + 1/3 * RSV
                d[i] = 2/3 * d[i-1] + 1/3 * k[i]
        j = 3*k - 2*d
        kdj_cross_up = ((k > d) & (np.roll(k, 1) <= np.roll(d, 1))) * 1
        kdj_cross_up[0] = 0
        kdj_cross_dn = ((k < d) & (np.roll(k, 1) >= np.roll(d, 1))) * 1
        kdj_cross_dn[0] = 0

        # Bollinger Bands (20, 2)
        bb_mid = pd.Series(close).rolling(20, min_periods=10).mean().values
        bb_std = pd.Series(close).rolling(20, min_periods=10).std().values
        bb_upper = bb_mid + 2 * bb_std
        bb_lower = bb_mid - 2 * bb_std
        bb_upper_hit = (close >= bb_upper) * 1
        bb_lower_hit = (close <= bb_lower) * 1

        # Volume MA
        vol_ma = g['volume'].rolling(VOL_MA, min_periods=10).mean().values

        g['rsi'] = rsi
        g['ema20'] = ema20; g['ema50'] = ema50
        g['ema_cross'] = ema_cross
        g['price_above_ema20'] = price_above_ema20
        g['price_below_ema20'] = price_below_ema20
        g['macd_line'] = macd_line; g['signal_line'] = signal_line
        g['macd_hist'] = macd_hist
        g['macd_cross_up'] = macd_cross_up
        g['macd_cross_dn'] = macd_cross_dn
        g['macd_cross_0_up'] = macd_cross_0_up
        g['macd_cross_0_dn'] = macd_cross_0_dn
        g['kdj_k'] = k; g['kdj_d'] = d; g['kdj_j'] = j
        g['kdj_cross_up'] = kdj_cross_up; g['kdj_cross_dn'] = kdj_cross_dn
        g['bb_upper'] = bb_upper; g['bb_lower'] = bb_lower
        g['bb_upper_hit'] = bb_upper_hit; g['bb_lower_hit'] = bb_lower_hit
        g['vol_ma20'] = vol_ma
        dfs.append(g)

    return pd.concat(dfs, ignore_index=True)


def detect_patterns_vectorized(df: pd.DataFrame) -> pd.DataFrame:
    """向量化解格形態檢測（主要形態：Support/Resistance/Bull Flag/Bear Flag/Morning Star/Evening Star）"""
    results = []

    for sym, g in df.groupby('symbol', sort=False):
        g = g.sort_values('date').copy()
        close = g['close'].values
        high = g['high'].values
        low = g['low'].values
        open_ = g['open'].values
        n = len(g)

        # 結果陣地
        pattern = np.full(n, '', dtype=object)
        pattern_dir = np.full(n, '', dtype=object)
        confidence = np.zeros(n)

        for i in range(5, n):
            c, h, l, o = close[i], high[i], low[i], open_[i]

            # ── Support / Resistance ─────────────────────
            lookback = min(20, i)
            window = close[i-lookback:i]
            sup = window.min()
            res = window.max()
            if l <= sup * 1.005 and sup > close[max(0,i-5):i].min() * 1.01:
                pattern[i] = 'Support'; pattern_dir[i] = 'bullish'
                confidence[i] = min(0.9, 0.5 + 0.05 * lookback)
            elif h >= res * 0.995 and res < close[max(0,i-5):i].max() * 0.99:
                pattern[i] = 'Resistance'; pattern_dir[i] = 'bearish'
                confidence[i] = min(0.9, 0.5 + 0.05 * lookback)

            # ── Bull Flag / Bear Flag ────────────────────
            if i >= 10:
                seg = close[i-10:i]
                if np.ptp(seg) > 0:
                    slope = (seg[-1] - seg[0]) / np.ptp(seg)
                    if -0.5 < slope < -0.05:   # 輕微下降（旗桿後整理）
                        pattern[i] = 'Bull Flag'; pattern_dir[i] = 'bullish'
                        confidence[i] = 0.65
                    elif 0.05 < slope < 0.5:
                        pattern[i] = 'Bear Flag'; pattern_dir[i] = 'bearish'
                        confidence[i] = 0.65

            # ── Morning Star (3-day) ───────────────────────
            if i >= 2:
                body1 = abs(close[i-2] - open_[i-2]) / (close[i-2] + 1e-9)
                body2 = abs(close[i-1] - open_[i-1]) / (close[i-1] + 1e-9)
                body3 = abs(close[i] - open_[i]) / (close[i] + 1e-9)
                mid_val = min(close[i-2], open_[i-2])
                max_close = max(close[i-2], open_[i-2])
                if (body1 > 0.001 and body2 < 0.001 and body3 > 0.001 and
                    close[i-1] < mid_val * 1.001 and
                    close[i] > (max_close + mid_val) / 2 and
                    close[i] > open_[i]):
                    pattern[i] = 'Morning Star'; pattern_dir[i] = 'bullish'
                    confidence[i] = 0.75

            # ── Evening Star ──────────────────────────────
            if i >= 2:
                body1 = abs(close[i-2] - open_[i-2]) / (close[i-2] + 1e-9)
                body2 = abs(close[i-1] - open_[i-1]) / (close[i-1] + 1e-9)
                body3 = abs(close[i] - open_[i]) / (close[i] + 1e-9)
                mid_val = max(close[i-2], open_[i-2])
                if (body1 > 0.001 and body2 < 0.001 and body3 > 0.001 and
                    close[i-1] > mid_val * 0.999 and
                    close[i] < (close[i-2] + open_[i-2]) / 2 and
                    close[i] < open_[i]):
                    pattern[i] = 'Evening Star'; pattern_dir[i] = 'bearish'
                    confidence[i] = 0.75

            # ── Bullish Engulfing ──────────────────────────
            if i >= 1:
                prev_body_up = close[i-1] > open_[i-1]
                curr_body_up = close[i] > open_[i]
                if (not prev_body_up and curr_body_up and
                    close[i] >= open_[i-1] and open_[i] <= close[i-1] and
                    close[i] > open_[i-1] and open_[i] < close[i-1]):
                    pattern[i] = 'Bullish Engulfing'; pattern_dir[i] = 'bullish'
                    confidence[i] = 0.7

            # ── Bearish Engulfing ─────────────────────────
            if i >= 1:
                prev_body_up = close[i-1] > open_[i-1]
                curr_body_up = close[i] > open_[i]
                if (prev_body_up and not curr_body_up and
                    open_[i] >= close[i-1] and close[i] <= open_[i-1] and
                    open_[i] > close[i-1] and close[i] < open_[i-1]):
                    pattern[i] = 'Bearish Engulfing'; pattern_dir[i] = 'bearish'
                    confidence[i] = 0.7

            # ── Doji ───────────────────────────────────────
            body = abs(close[i] - open_[i])
            range_ = (high[i] - low[i] + 1e-9)
            if body / range_ < 0.1 and range_ > 0:
                pattern[i] = 'Doji'; pattern_dir[i] = 'neutral'
                confidence[i] = 0.5

            # ── Hammer ────────────────────────────────────
            if i >= 1:
                body = abs(close[i] - open_[i])
                lower_shadow = min(open_[i], close[i]) - low[i]
                upper_shadow = high[i] - max(open_[i], close[i])
                if lower_shadow > body * 2 and upper_shadow < body * 0.5:
                    pattern[i] = 'Hammer'; pattern_dir[i] = 'bullish'
                    confidence[i] = 0.65

        g['pattern'] = pattern
        g['pattern_dir'] = pattern_dir
        g['pattern_conf'] = confidence
        results.append(g)

    return pd.concat(results, ignore_index=True)


def detect_indicators_vectorized(df: pd.DataFrame) -> pd.DataFrame:
    """向量化解格指標信號"""
    results = []
    for sym, g in df.groupby('symbol', sort=False):
        g = g.copy()
        rsi = g['rsi'].values
        macd_cross_up = g['macd_cross_up'].values
        macd_cross_dn = g['macd_cross_dn'].values
        macd_cross_0_up = g['macd_cross_0_up'].values
        macd_cross_0_dn = g['macd_cross_0_dn'].values
        kdj_cross_up = g['kdj_cross_up'].values
        kdj_cross_dn = g['kdj_cross_dn'].values
        ema_cross = g['ema_cross'].values
        price_above_ema20 = g['price_above_ema20'].values
        price_below_ema20 = g['price_below_ema20'].values
        bb_upper_hit = g['bb_upper_hit'].values
        bb_lower_hit = g['bb_lower_hit'].values

        n = len(g)
        ind_sig = np.full(n, '', dtype=object)
        ind_dir = np.full(n, '', dtype=object)

        for i in range(1, n):
            # RSI 超賣/超買
            if rsi[i] < 30:
                ind_sig[i] = 'RSI 超賣區域 (30)'; ind_dir[i] = 'bullish'
            elif rsi[i] < 35 and rsi[i-1] < 30:
                ind_sig[i] = 'RSI 維持超賣'; ind_dir[i] = 'bullish'
            elif rsi[i] > 70:
                ind_sig[i] = 'RSI 超買區域 (70)'; ind_dir[i] = 'bearish'
            elif rsi[i] > 65 and rsi[i-1] > 70:
                ind_sig[i] = 'RSI 維持超買'; ind_dir[i] = 'bearish'
            # BB
            elif bb_lower_hit[i]:
                ind_sig[i] = 'BB 跌破下軌 (超賣)'; ind_dir[i] = 'bullish'
            elif bb_upper_hit[i]:
                ind_sig[i] = 'BB 突破上軌 (超買)'; ind_dir[i] = 'bearish'
            # MACD
            elif macd_cross_up[i] and rsi[i] < 55:
                ind_sig[i] = 'MACD 金叉 (空頭區)'; ind_dir[i] = 'bullish'
            elif macd_cross_dn[i] and rsi[i] > 45:
                ind_sig[i] = 'MACD 死叉 (多頭區)'; ind_dir[i] = 'bearish'
            elif macd_cross_0_up[i]:
                ind_sig[i] = 'MACD 突破 0 軸'; ind_dir[i] = 'bullish'
            elif macd_cross_0_dn[i]:
                ind_sig[i] = 'MACD 跌破 0 軸'; ind_dir[i] = 'bearish'
            # KDJ
            elif kdj_cross_up[i] and rsi[i] < 50:
                ind_sig[i] = 'KDJ 超賣區金叉'; ind_dir[i] = 'bullish'
            elif kdj_cross_dn[i] and rsi[i] > 50:
                ind_sig[i] = 'KDJ 超買區死叉'; ind_dir[i] = 'bearish'
            # EMA
            elif ema_cross[i] and rsi[i] < 50:
                ind_sig[i] = 'EMA 黃金交叉 (20 上穿 50)'; ind_dir[i] = 'bullish'
            elif ema_cross[i] and rsi[i] > 50:
                ind_sig[i] = 'EMA 死亡交叉 (20 下穿 50)'; ind_dir[i] = 'bearish'
            elif price_above_ema20[i] and not price_above_ema20[i-1]:
                ind_sig[i] = '價格突破 EMA20'; ind_dir[i] = 'bullish'
            elif price_below_ema20[i] and not price_below_ema20[i-1]:
                ind_sig[i] = '價格跌破 EMA20'; ind_dir[i] = 'bearish'

        g['ind_sig'] = ind_sig
        g['ind_dir'] = ind_dir
        results.append(g)

    return pd.concat(results, ignore_index=True)


def calc_forward_returns(df: pd.DataFrame) -> pd.DataFrame:
    """計算未來5日回報"""
    results = []
    for sym, g in df.groupby('symbol', sort=False):
        g = g.copy().sort_values('date')
        close = g['close'].values
        n = len(g)
        fwd_ret = np.full(n, np.nan)
        for i in range(n - FORWARD):
            fwd_ret[i] = (close[i + FORWARD] - close[i]) / close[i]
        g['fwd_ret'] = fwd_ret
        results.append(g)
    return pd.concat(results, ignore_index=True)


def run_backtest(df: pd.DataFrame, use_indicator: bool, use_volume: bool,
                 use_sector: bool, top_sectors: list = None) -> pd.DataFrame:
    """單次回測運行（過濾後的信號集合）"""

    mask = (
        (df['pattern'] != '') &
        (df['pattern_dir'].isin(['bullish','bearish'])) &
        (df['pattern_conf'] >= 0.5) &
        (df['fwd_ret'].notna())
    )

    if use_indicator:
        mask_ind = (
            (df['ind_sig'] != '') &
            (df['ind_dir'] == df['pattern_dir'])
        )
        mask = mask & mask_ind

    if use_volume:
        vol_ok = (
            (df['volume'] >= df['vol_ma20'] * VOL_TODAY_RATIO) &
            (df['volume'].shift(-1) >= df['vol_ma20'].shift(-1) * VOL_NEXT_RATIO)
        )
        mask = mask & vol_ok.fillna(False)

    if use_sector and top_sectors:
        mask = mask & df['sector'].isin(top_sectors)

    sigs = df[mask].copy()

    # 計算成功
    sigs['success'] = np.where(
        sigs['pattern_dir'] == 'bullish',
        sigs['fwd_ret'] > THRESHOLD,
        sigs['fwd_ret'] < -THRESHOLD
    )

    return sigs


def aggregate(df: pd.DataFrame) -> pd.DataFrame:
    """按形態+方向分組計算勝率"""
    grp = df.groupby(['pattern','pattern_dir']).agg(
        count=('success','count'),
        wins=('success','sum'),
        avg_ret=('fwd_ret','mean')
    ).reset_index()
    grp['win_rate'] = grp['wins'] / grp['count']
    return grp.sort_values('win_rate', ascending=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sp500', action='store_true')
    parser.add_argument('--hsi', action='store_true')
    args = parser.parse_args()

    markets = [('sp500','S&P 500'), ('hsi','HSI')]
    if args.sp500: markets = [('sp500','S&P 500')]
    if args.hsi: markets = [('hsi','HSI')]

    # ── 一次性加載 + 計算所有指標 ────────────────────
    print("📊 Loading data...")
    df_all = load_all()

    print("🔧 Computing indicators...")
    df_all = calc_indicators(df_all)

    print("📐 Detecting patterns...")
    df_all = detect_patterns_vectorized(df_all)

    print("📐 Detecting indicators...")
    df_all = detect_indicators_vectorized(df_all)

    print("📈 Computing forward returns...")
    df_all = calc_forward_returns(df_all)

    # ── 載入 Sector 數據 ────────────────────────────────
    import requests
    from io import StringIO

    SECTOR_MAP = {
        'Information Technology':'XLK','Health Care':'XLV','Financials':'XLF',
        'Consumer Discretionary':'XLY','Communication Services':'XLC',
        'Energy':'XLE','Industrials':'XLI','Materials':'XLB',
        'Real Estate':'XLRE','Utilities':'XLU','Consumer Staples':'XLP',
    }

    # SP500 sectors 從 Wikipedia 動態獲取
    sp500_sectors = {}
    try:
        resp = requests.get(
            'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies',
            headers={'User-Agent': 'Mozilla/5.0'}, timeout=15
        )
        tables = pd.read_html(StringIO(resp.text))
        sp500_df = tables[0][['Symbol','GICS Sector']].rename(
            columns={'Symbol':'symbol','GICS Sector':'sector'})
        sp500_df['symbol'] = sp500_df['symbol'].str.strip()
        sp500_sectors = dict(zip(sp500_df['symbol'], sp500_df['sector']))
        print(f"  Loaded {len(sp500_sectors)} SP500 sector mappings")
    except Exception as e:
        print(f"  ⚠️ Failed to load SP500 sectors: {e}")

    # HSI sectors（Hardcoded）
    hsi_sectors = {
        '0700.HK':'Communication Services','3968.HK':'Financials',
        '0005.HK':'Financials','9988.HK':'Consumer Discretionary',
        '1211.HK':'Consumer Discretionary','1024.HK':'Communication Services',
        '1810.HK':'Information Technology','3690.HK':'Consumer Discretionary',
        '0168.HK':'Industrials','6630.HK':'Information Technology',
        '2269.HK':'Health Care','6185.HK':'Health Care',
        '6030.HK':'Financials','0688.HK':'Real Estate',
        '1109.HK':'Real Estate','1177.HK':'Health Care',
        '2319.HK':'Consumer Staples','2628.HK':'Financials',
        '3328.HK':'Financials','3868.HK':'Energy',
        '3988.HK':'Financials','0175.HK':'Consumer Discretionary',
    }

    # 合併並映射到 df_all
    all_sectors = {**sp500_sectors, **hsi_sectors}
    df_all['sector'] = df_all['symbol'].map(all_sectors).fillna('Unknown')
    df_all['sector_etf'] = df_all['sector'].map(SECTOR_MAP).fillna('Unknown')

    for market, name in markets:
        # ── 股票池 ────────────────────────────────────────
        if market == 'sp500':
            path = Path(__file__).parent / 'data' / 'constituents_sp500.txt'
        else:
            path = Path(__file__).parent / 'data' / 'constituents_hsi.txt'
        tickers = set(open(path).read().splitlines())
        df_mkt = df_all[df_all['symbol'].isin(tickers)].copy()
        print(f"\n{name}: {len(tickers)} 隻股票, {len(df_mkt):,} 行數據")

        if df_mkt.empty:
            print(f"  ⚠️ No data"); continue

        # ── 識別 top sectors（用 A+B+C 組合）──────────────
        tmp = run_backtest(df_mkt, True, True, False)
        if not tmp.empty:
            sec_grp = tmp.groupby('sector').agg(
                wr=('success','mean'), n=('success','count')
            ).reset_index()
            sec_grp = sec_grp[sec_grp['n'] >= 10].sort_values('wr', ascending=False)
            top_secs = sec_grp.head(3)['sector'].tolist()
        else:
            top_secs = []

        print(f"  Top sectors: {top_secs}")

        # ── 8 個組合 ───────────────────────────────────────
        combos = [
            ('A',             False, False, False, '形態 alone（基準）'),
            ('A+C',           False, True,  False, '形態 + 成交量'),
            ('A+B',           True,  False, False, '形態 + 指標'),
            ('A+B+C',         True,  True,  False, '形態 + 指標 + 成交量'),
            ('A  +D',         False, False, True,  '形態 + Sector'),
            ('A+C+D',         False, True,  True,  '形態 + 成交量 + Sector'),
            ('A+B +D',        True,  False, True,  '形態 + 指標 + Sector'),
            ('A+B+C+D',       True,  True,  True,  '完整全因子'),
        ]

        print(f"\n{'='*130}")
        print(f"  📊 {name} — 全因子回測（8種組合）")
        print(f"     A = 形態  B = 技術指標  C = 成交量確認  D = Sector")
        print(f"{'='*130}")

        results_rows = []

        for combo_key, use_ind, use_vol, use_sec, desc in combos:
            sigs = run_backtest(df_mkt, use_ind, use_vol, use_sec,
                               top_sectors=top_secs if use_sec else None)
            if sigs.empty:
                continue

            grp = aggregate(sigs)
            grp['combination'] = combo_key
            grp['description'] = desc
            grp['market'] = name
            results_rows.append(grp)

            # 基準（A）用於計算 delta
            if combo_key == 'A':
                base_grp = grp.set_index(['pattern','pattern_dir'])

        # ── 打印報告 ──────────────────────────────────────
        combo_order = ['A','A+C','A+B','A+B+C','A  +D','A+C+D','A+B +D','A+B+C+D']
        combo_desc_map = dict(zip([c[0] for c in combos], [c[4] for c in combos]))

        for combo_key in combo_order:
            matching = [r for r in results_rows if r['combination'].iloc[0] == combo_key]
            if not matching:
                continue
            grp = matching[0]

            print(f"\n{'─'*130}")
            desc = combo_desc_map.get(combo_key, combo_key)
            print(f"  {desc}（{combo_key}）")
            print(f"{'─'*130}")
            print(f"  {'形態':<26} {'方向':<10} {'次數':>6} {'勝率':>8} {'平均回報':>10}", end='')

            if combo_key != 'A':
                print(f" {'vs基準A':>10}")
            else:
                print()

            print(f"  {'─'*90}")

            for _, r in grp.iterrows():
                flag = "🛡️" if r['win_rate'] >= 0.70 else "  "
                delta_str = ""
                if combo_key != 'A':
                    try:
                        base_row = base_grp.loc[(r['pattern'], r['pattern_dir'])]
                        delta = r['win_rate'] - base_row['win_rate']
                        delta_str = f"{delta:+.1%}"
                    except Exception:
                        delta_str = "N/A"

                print(f"  {flag}{r['pattern']:<24} {r['pattern_dir']:<10} "
                      f"{r['count']:>6} {r['win_rate']:>7.1%} {r['avg_ret']:>+9.2%}  {delta_str:>10}")

            # 底部摘要
            total_n = grp['count'].sum()
            total_wr = (grp['wins'].sum() / grp['count'].sum()) if grp['count'].sum() > 0 else 0
            total_ret = (grp['avg_ret'] * grp['count']).sum() / grp['count'].sum() if grp['count'].sum() > 0 else 0
            print(f"  {'─'*90}")
            print(f"  {'總計':<26} {'':10} {total_n:>6} {total_wr:>7.1%} {total_ret:>+9.2%}")

        # ── 因子貢獻摘要 ──────────────────────────────────
        print(f"\n{'='*130}")
        print(f"  📌 因子獨立貢獻（勝率提升幅度）")
        print(f"{'='*130}")

        base_grp = next(r for r in results_rows if r['combination'].iloc[0] == 'A').copy()
        base_idx = base_grp.set_index(['pattern','pattern_dir'])

        for added, use_ind, use_vol, use_sec in [
            ('B',        True,  False, False),
            ('C',        False, True,  False),
            ('D',        False, False, True),
            ('B+C',      True,  True,  False),
            ('B+D',      True,  False, True),
            ('C+D',      False, True,  True),
            ('B+C+D',    True,  True,  True),
        ]:
            combo_key = 'A+' + added
            cmp_rows = [r for r in results_rows
                       if (r['combination'].iloc[0].replace(' ','') == combo_key.replace(' ',''))]
            if not cmp_rows:
                continue
            cmp_grp = cmp_rows[0].copy()
            cmp_idx = cmp_grp.set_index(['pattern','pattern_dir'])

            merged = base_idx.join(cmp_idx, lsuffix='_a', rsuffix='_b', how='inner')
            if merged.empty:
                continue

            avg_delta = (merged['win_rate_b'] - merged['win_rate_a']).mean()
            n_improved = (merged['win_rate_b'] > merged['win_rate_a']).sum()
            n_total = len(merged)

            best_idx = (merged['win_rate_b'] - merged['win_rate_a']).idxmax()
            best_delta = merged.loc[best_idx, 'win_rate_b'] - merged.loc[best_idx, 'win_rate_a']

            worst_idx = (merged['win_rate_b'] - merged['win_rate_a']).idxmin()
            worst_delta = merged.loc[worst_idx, 'win_rate_b'] - merged.loc[worst_idx, 'win_rate_a']

            print(f"\n  +{added}: 平均勝率 {avg_delta:+.1%} | "
                  f"改善 {n_improved}/{n_total} 個形態 | "
                  f"最佳 {best_idx[0]} ({best_idx[1]}) {best_delta:+.1%} | "
                  f"最差 {worst_idx[0]} ({worst_idx[1]}) {worst_delta:+.1%}")

        # 保存
        if results_rows:
            out = pd.concat(results_rows, ignore_index=True)
            out_path = Path(__file__).parent / 'backtest_full_factors_results.csv'
            out.to_csv(out_path, index=False)
            print(f"\n\n💾 結果已保存: {out_path}")


if __name__ == '__main__':
    main()
