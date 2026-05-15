"""
indicator_calculator.py — 技術指標計算模組
==========================================
支持指標：RSI, MACD, KDJ, EMA, SMA, ATR, Bollinger Bands
"""

from __future__ import annotations
import pandas as pd
import numpy as np
from typing import Optional


def calculate_rsi(prices: pd.Series, period: int = 14) -> pd.Series:
    """
    RSI (Relative Strength Index)
    - 超買: RSI > 70
    - 超賣: RSI < 30
    - 中性: 30 <= RSI <= 70
    """
    delta = prices.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)

    avg_gain = gain.rolling(window=period, min_periods=period).mean()
    avg_loss = loss.rolling(window=period, min_periods=period).mean()

    # EMA 方式 (更平滑)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def calculate_macd(prices: pd.Series,
                   fast: int = 12,
                   slow: int = 26,
                   signal: int = 9) -> pd.DataFrame:
    """
    MACD (Moving Average Convergence Divergence)
    - MACD Line = EMA_fast - EMA_slow
    - Signal Line = EMA(MACD, 9)
    - Histogram = MACD - Signal
    - 金叉 (Bullish): MACD > Signal
    - 死叉 (Bearish): MACD < Signal
    """
    ema_fast = prices.ewm(span=fast, adjust=False, min_periods=fast).mean()
    ema_slow = prices.ewm(span=slow, adjust=False, min_periods=slow).mean()

    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    histogram = macd_line - signal_line

    return pd.DataFrame({
        'macd': macd_line,
        'signal': signal_line,
        'histogram': histogram
    })


def calculate_kdj(high: pd.Series, low: pd.Series, close: pd.Series,
                  period: int = 9) -> pd.DataFrame:
    """
    KDJ (Stochastic Oscillator)
    - K: 快速隨機指標
    - D: 慢速隨機指標 (K 的平滑)
    - J: 3*K - 2*D
    - 超買: K/D/J > 80
    - 超賣: K/D/J < 20
    - 金叉 (Bullish): K 從下突破 D
    - 死叉 (Bearish): K 從上跌破 D
    """
    lowest_low = low.rolling(window=period, min_periods=period).min()
    highest_high = high.rolling(window=period, min_periods=period).max()

    rsv = (close - lowest_low) / (highest_high - lowest_low) * 100
    rsv = rsv.fillna(50)

    k = rsv.ewm(alpha=1/3, adjust=False, min_periods=0).mean()
    d = k.ewm(alpha=1/3, adjust=False, min_periods=0).mean()
    j = 3 * k - 2 * d

    return pd.DataFrame({
        'k': k,
        'd': d,
        'j': j
    })


def calculate_ema(prices: pd.Series, period: int) -> pd.Series:
    """EMA (Exponential Moving Average)"""
    return prices.ewm(span=period, adjust=False, min_periods=period).mean()


def calculate_sma(prices: pd.Series, period: int) -> pd.Series:
    """SMA (Simple Moving Average)"""
    return prices.rolling(window=period, min_periods=period).mean()


def calculate_bollinger_bands(prices: pd.Series,
                               period: int = 20,
                               std_dev: float = 2.0) -> pd.DataFrame:
    """
    Bollinger Bands
    - Upper = SMA + 2*STD
    - Middle = SMA
    - Lower = SMA - 2*STD
    - 突破上軌 (Bullish): Close > Upper
    - 跌破下軌 (Bearish): Close < Lower
    """
    sma = prices.rolling(window=period, min_periods=period).mean()
    std = prices.rolling(window=period, min_periods=period).std()

    upper = sma + std_dev * std
    lower = sma - std_dev * std

    return pd.DataFrame({
        'upper': upper,
        'middle': sma,
        'lower': lower
    })


def calculate_atr(high: pd.Series, low: pd.Series, close: pd.Series,
                  period: int = 14) -> pd.Series:
    """
    ATR (Average True Range)
    - 衡量市場波動性
    """
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))

    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=period, min_periods=period).mean()

    return atr


def calculate_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    為完整股價 DataFrame 計算所有技術指標
    輸入欄位: open, high, low, close, volume
    返回: 含所有指標的 DataFrame
    """
    result = df.copy()

    # 確保所有價格欄位為 float（SQLite 可能返回 decimal.Decimal）
    for col in ["open", "high", "low", "close", "volume"]:
        if col in result.columns:
            result[col] = pd.to_numeric(result[col], errors="coerce").astype(float)

    # 確保所有價格欄位為 float（SQLite 可能返回 decimal.Decimal）
    for col in ["open", "high", "low", "close", "volume"]:
        if col in result.columns:
            result[col] = pd.to_numeric(result[col], errors="coerce").astype(float)

    # RSI
    result['rsi_14'] = calculate_rsi(result['close'], period=14)

    # MACD
    macd = calculate_macd(result['close'])
    result['macd'] = macd['macd']
    result['macd_signal'] = macd['signal']
    result['macd_histogram'] = macd['histogram']

    # KDJ — 使用已轉換的 result 欄位
    kdj = calculate_kdj(result['high'], result['low'], result['close'])
    result['kdj_k'] = kdj['k']
    result['kdj_d'] = kdj['d']
    result['kdj_j'] = kdj['j']

    # EMA
    result['ema_20'] = calculate_ema(result['close'], 20)
    result['ema_50'] = calculate_ema(result['close'], 50)
    result['ema_200'] = calculate_ema(result['close'], 200)

    # SMA
    result['sma_20'] = calculate_sma(result['close'], 20)
    result['sma_50'] = calculate_sma(result['close'], 50)

    # Bollinger Bands
    bb = calculate_bollinger_bands(result['close'])
    result['bb_upper'] = bb['upper']
    result['bb_middle'] = bb['middle']
    result['bb_lower'] = bb['lower']

    # ATR
    result['atr_14'] = calculate_atr(result['high'], result['low'], result['close'])

    return result
