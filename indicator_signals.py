"""
indicator_signals.py — 技術指標信號檢測模組
==========================================
定義所有技術指標的信號邏輯及信心度計算

信號類型:
  - RSI: 超買(>70), 超賣(<30), 黃金交叉/死亡交叉
  - MACD: 金叉(>0), 死叉(<0), 底背離/頂背離
  - KDJ: 超買(>80), 超賣(<20), 金叉/死叉
  - EMA: 多頭排列(短>長), 空頭排列(短<長), 價格突破
  - BB: 突破上軌, 跌破下軌, 收緊/擴張
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple
import pandas as pd
import numpy as np


@dataclass
class IndicatorSignal:
    """單一技術指標信號"""
    indicator: str           # 'RSI', 'MACD', 'KDJ', 'EMA', 'BB'
    signal_type: str         # 'bullish', 'bearish', 'neutral'
    name: str                # 具體信號名稱
    confidence: float        # 0.0 ~ 1.0
    metadata: dict = field(default_factory=dict)

    def label(self) -> str:
        emoji = "🟢" if self.signal_type == "bullish" else ("🔴" if self.signal_type == "bearish" else "🟡")
        return f"{emoji} {self.indicator}: {self.name}"


def detect_rsi_signals(df: pd.DataFrame, idx: int) -> List[IndicatorSignal]:
    """
    RSI 信號檢測
    - RSI > 70: 超買 → 看跌
    - RSI < 30: 超賣 → 看漲
    - RSI 從 <30 上穿: 強烈看漲
    - RSI 從 >70 下穿: 強烈看跌
    - RSI 50 中性
    - RSI 50-70 維持強勢（動能版，新增）
    - RSI 加速上升（動能版，新增）
    """
    signals = []

    if idx < 14:
        return signals

    rsi = df['rsi_14'].iloc[idx]
    rsi_prev = df['rsi_14'].iloc[idx - 1] if idx > 0 else rsi
    rsi_5_ago = df['rsi_14'].iloc[idx - 5] if idx >= 5 else rsi_prev

    if pd.isna(rsi):
        return signals

    # ── 超買信號（現有）──
    if rsi > 70:
        if rsi_prev <= 70:
            signals.append(IndicatorSignal(
                indicator='RSI',
                signal_type='bearish',
                name='RSI 超買區域 (70)',
                confidence=0.70,
                metadata={'rsi': rsi, 'threshold': 70}
            ))
        else:
            signals.append(IndicatorSignal(
                indicator='RSI',
                signal_type='bearish',
                name='RSI 維持超買',
                confidence=0.55,
                metadata={'rsi': rsi, 'threshold': 70}
            ))

    # ── 超賣信號（現有）──
    elif rsi < 30:
        if rsi_prev >= 30:
            signals.append(IndicatorSignal(
                indicator='RSI',
                signal_type='bullish',
                name='RSI 超賣區域 (30)',
                confidence=0.75,
                metadata={'rsi': rsi, 'threshold': 30}
            ))
        else:
            signals.append(IndicatorSignal(
                indicator='RSI',
                signal_type='bullish',
                name='RSI 維持超賣',
                confidence=0.55,
                metadata={'rsi': rsi, 'threshold': 30}
            ))

    # RSI 黃金交叉 50（從下穿上）
    elif rsi_prev < 50 <= rsi:
        signals.append(IndicatorSignal(
            indicator='RSI',
            signal_type='bullish',
            name='RSI 上穿 50 中性線',
            confidence=0.55,
            metadata={'rsi': rsi}
        ))

    # RSI 死亡交叉 50（從上下穿）
    elif rsi_prev > 50 >= rsi:
        signals.append(IndicatorSignal(
            indicator='RSI',
            signal_type='bearish',
            name='RSI 下穿 50 中性線',
            confidence=0.55,
            metadata={'rsi': rsi}
        ))

    # ── ✨ 新增：動能 RSI 信號 ──

    # RSI 維持強勢 (50-70)：健康上升區間，不超買不超賣
    if 50 <= rsi <= 70:
        signals.append(IndicatorSignal(
            indicator='RSI',
            signal_type='bullish',
            name='RSI 維持強勢 (50-70)',
            confidence=0.60,
            metadata={'rsi': rsi}
        ))

    # RSI 加速上升：5 天前 RSI 低於現在（動能加速）
    if idx >= 5 and not pd.isna(rsi_5_ago) and rsi - rsi_5_ago > 5:
        signals.append(IndicatorSignal(
            indicator='RSI',
            signal_type='bullish',
            name='RSI 加速上升',
            confidence=0.65,
            metadata={'rsi': rsi, 'rsi_5_ago': rsi_5_ago, 'delta': rsi - rsi_5_ago}
        ))

    # RSI 由強勢區（50-70）進一步加速：昨日在 50-70，今日明顯上升
    if (50 <= rsi_prev <= 70) and (rsi - rsi_prev) > 3:
        signals.append(IndicatorSignal(
            indicator='RSI',
            signal_type='bullish',
            name='RSI 動能加速（強勢區）',
            confidence=0.60,
            metadata={'rsi': rsi, 'rsi_prev': rsi_prev, 'delta': rsi - rsi_prev}
        ))

    return signals


def detect_macd_signals(df: pd.DataFrame, idx: int) -> List[IndicatorSignal]:
    """
    MACD 信號檢測
    - MACD > 0 & MACD > Signal: 多頭區域
    - MACD < 0 & MACD < Signal: 空頭區域
    - MACD 金叉 Signal (MACD 從下穿上): 強烈看漲
    - MACD 死叉 Signal (MACD 從上下穿): 強烈看跌
    - MACD 突破 0 軸: 動量增強
    """
    signals = []

    if idx < 26:
        return signals

    macd = df['macd'].iloc[idx]
    signal = df['macd_signal'].iloc[idx]
    macd_prev = df['macd'].iloc[idx - 1] if idx > 0 else macd
    signal_prev = df['macd_signal'].iloc[idx - 1] if idx > 0 else signal

    if pd.isna(macd) or pd.isna(signal):
        return signals

    # MACD 金叉 (MACD 從下穿上 Signal)
    if macd_prev <= signal_prev and macd > signal:
        if macd > 0:
            signals.append(IndicatorSignal(
                indicator='MACD',
                signal_type='bullish',
                name='MACD 金叉 (多頭區)',
                confidence=0.80,
                metadata={'macd': macd, 'signal': signal}
            ))
        else:
            signals.append(IndicatorSignal(
                indicator='MACD',
                signal_type='bullish',
                name='MACD 金叉 (空頭區)',
                confidence=0.60,
                metadata={'macd': macd, 'signal': signal}
            ))

    # MACD 死叉 (MACD 從上下穿 Signal)
    elif macd_prev >= signal_prev and macd < signal:
        if macd < 0:
            signals.append(IndicatorSignal(
                indicator='MACD',
                signal_type='bearish',
                name='MACD 死叉 (空頭區)',
                confidence=0.80,
                metadata={'macd': macd, 'signal': signal}
            ))
        else:
            signals.append(IndicatorSignal(
                indicator='MACD',
                signal_type='bearish',
                name='MACD 死叉 (多頭區)',
                confidence=0.60,
                metadata={'macd': macd, 'signal': signal}
            ))

    # MACD 突破 0 軸
    if macd_prev < 0 <= macd:
        signals.append(IndicatorSignal(
            indicator='MACD',
            signal_type='bullish',
            name='MACD 突破 0 軸',
            confidence=0.70,
            metadata={'macd': macd, 'signal': signal}
        ))
    elif macd_prev > 0 >= macd:
        signals.append(IndicatorSignal(
            indicator='MACD',
            signal_type='bearish',
            name='MACD 跌破 0 軸',
            confidence=0.70,
            metadata={'macd': macd, 'signal': signal}
        ))

    return signals


def detect_kdj_signals(df: pd.DataFrame, idx: int) -> List[IndicatorSignal]:
    """
    KDJ 信號檢測
    - K > 80: 超買
    - K < 20: 超賣
    - K 金叉 D: 看漲
    - K 死叉 D: 看跌
    - J > 100 或 J < 0: 極值
    """
    signals = []

    if idx < 9:
        return signals

    k = df['kdj_k'].iloc[idx]
    d = df['kdj_d'].iloc[idx]
    j = df['kdj_j'].iloc[idx]

    k_prev = df['kdj_k'].iloc[idx - 1] if idx > 0 else k
    d_prev = df['kdj_d'].iloc[idx - 1] if idx > 0 else d

    if pd.isna(k) or pd.isna(d):
        return signals

    # KDJ 金叉 (K 從下穿上 D)
    if k_prev <= d_prev and k > d:
        if k < 20:
            signals.append(IndicatorSignal(
                indicator='KDJ',
                signal_type='bullish',
                name='KDJ 超賣區金叉',
                confidence=0.80,
                metadata={'k': k, 'd': d, 'j': j}
            ))
        elif k > 80:
            signals.append(IndicatorSignal(
                indicator='KDJ',
                signal_type='neutral',
                name='KDJ 超買區金叉 (謹慎)',
                confidence=0.40,
                metadata={'k': k, 'd': d, 'j': j}
            ))
        else:
            signals.append(IndicatorSignal(
                indicator='KDJ',
                signal_type='bullish',
                name='KDJ 金叉',
                confidence=0.65,
                metadata={'k': k, 'd': d, 'j': j}
            ))

    # KDJ 死叉 (K 從上下穿 D)
    elif k_prev >= d_prev and k < d:
        if k > 80:
            signals.append(IndicatorSignal(
                indicator='KDJ',
                signal_type='bearish',
                name='KDJ 超買區死叉',
                confidence=0.80,
                metadata={'k': k, 'd': d, 'j': j}
            ))
        elif k < 20:
            signals.append(IndicatorSignal(
                indicator='KDJ',
                signal_type='neutral',
                name='KDJ 超賣區死叉 (謹慎)',
                confidence=0.40,
                metadata={'k': k, 'd': d, 'j': j}
            ))
        else:
            signals.append(IndicatorSignal(
                indicator='KDJ',
                signal_type='bearish',
                name='KDJ 死叉',
                confidence=0.65,
                metadata={'k': k, 'd': d, 'j': j}
            ))

    # J 極值
    if j > 100:
        signals.append(IndicatorSignal(
            indicator='KDJ',
            signal_type='bearish',
            name='KDJ J 值極高 (>100)',
            confidence=0.60,
            metadata={'j': j}
        ))
    elif j < 0:
        signals.append(IndicatorSignal(
            indicator='KDJ',
            signal_type='bullish',
            name='KDJ J 值極低 (<0)',
            confidence=0.60,
            metadata={'j': j}
        ))

    return signals


def detect_ema_signals(df: pd.DataFrame, idx: int) -> List[IndicatorSignal]:
    """
    EMA 信號檢測
    - 多頭排列: EMA20 > EMA50 > EMA200
    - 空頭排列: EMA20 < EMA50 < EMA200
    - 價格突破 EMA20/50: 短期動量
    - EMA20 上穿 EMA50: 黃金交叉
    - EMA20 下穿 EMA50: 死亡交叉
    """
    signals = []

    if idx < 50:
        return signals

    ema_20 = df['ema_20'].iloc[idx]
    ema_50 = df['ema_50'].iloc[idx]
    ema_200 = df['ema_200'].iloc[idx]
    close = df['close'].iloc[idx]

    ema_20_prev = df['ema_20'].iloc[idx - 1] if idx > 0 else ema_20
    ema_50_prev = df['ema_50'].iloc[idx - 1] if idx > 0 else ema_50

    if pd.isna(ema_20) or pd.isna(ema_50) or pd.isna(ema_200):
        return signals

    # EMA 多頭排列
    if ema_20 > ema_50 > ema_200:
        signals.append(IndicatorSignal(
            indicator='EMA',
            signal_type='bullish',
            name='EMA 多頭排列 (20>50>200)',
            confidence=0.75,
            metadata={'ema_20': ema_20, 'ema_50': ema_50, 'ema_200': ema_200}
        ))

    # EMA 空頭排列
    elif ema_20 < ema_50 < ema_200:
        signals.append(IndicatorSignal(
            indicator='EMA',
            signal_type='bearish',
            name='EMA 空頭排列 (20<50<200)',
            confidence=0.75,
            metadata={'ema_20': ema_20, 'ema_50': ema_50, 'ema_200': ema_200}
        ))

    # EMA20 上穿 EMA50 (黃金交叉)
    if ema_20_prev <= ema_50_prev and ema_20 > ema_50:
        signals.append(IndicatorSignal(
            indicator='EMA',
            signal_type='bullish',
            name='EMA 黃金交叉 (20 上穿 50)',
            confidence=0.70,
            metadata={'ema_20': ema_20, 'ema_50': ema_50}
        ))

    # EMA20 下穿 EMA50 (死亡交叉)
    elif ema_20_prev >= ema_50_prev and ema_20 < ema_50:
        signals.append(IndicatorSignal(
            indicator='EMA',
            signal_type='bearish',
            name='EMA 死亡交叉 (20 下穿 50)',
            confidence=0.70,
            metadata={'ema_20': ema_20, 'ema_50': ema_50}
        ))

    # 價格突破 EMA20
    if close > ema_20 and close_prev_check(df, idx, 'ema_20'):
        signals.append(IndicatorSignal(
            indicator='EMA',
            signal_type='bullish',
            name='價格突破 EMA20',
            confidence=0.55,
            metadata={'close': close, 'ema_20': ema_20}
        ))
    elif close < ema_20 and close_prev_check(df, idx, 'ema_20'):
        signals.append(IndicatorSignal(
            indicator='EMA',
            signal_type='bearish',
            name='價格跌破 EMA20',
            confidence=0.55,
            metadata={'close': close, 'ema_20': ema_20}
        ))

    return signals


def close_prev_check(df: pd.DataFrame, idx: int, col: str, lookback: int = 1) -> bool:
    """檢查價格相對於某條均線的過去狀態"""
    if idx <= lookback:
        return False
    close_now = df['close'].iloc[idx]
    val_now = df[col].iloc[idx]
    val_prev = df[col].iloc[idx - lookback]
    close_prev = df['close'].iloc[idx - lookback]
    if pd.isna(val_now) or pd.isna(val_prev):
        return False
    # 檢查是否剛好穿過
    return (close_now > val_now and close_prev <= val_prev) or \
           (close_now < val_now and close_prev >= val_prev)


def detect_bb_signals(df: pd.DataFrame, idx: int) -> List[IndicatorSignal]:
    """
    Bollinger Bands 信號檢測
    - 價格突破上軌: 超買信號
    - 價格跌破下軌: 超賣信號
    -  bands 收窄: 波動性低，突破將至
    -  bands 擴寬: 波動性高
    - 價格在中軌上方（動能版，新增）
    - BB 中軌向上（動能版，新增）
    """
    signals = []

    if idx < 20:
        return signals

    close = df['close'].iloc[idx]
    bb_upper = df['bb_upper'].iloc[idx]
    bb_lower = df['bb_lower'].iloc[idx]
    bb_middle = df['bb_middle'].iloc[idx]

    if pd.isna(bb_upper) or pd.isna(bb_lower):
        return signals

    # 突破上軌
    if close > bb_upper:
        signals.append(IndicatorSignal(
            indicator='BB',
            signal_type='bearish',
            name='BB 突破上軌 (超買)',
            confidence=0.65,
            metadata={'close': close, 'bb_upper': bb_upper}
        ))

    # 跌破下軌
    elif close < bb_lower:
        signals.append(IndicatorSignal(
            indicator='BB',
            signal_type='bullish',
            name='BB 跌破下軌 (超賣)',
            confidence=0.65,
            metadata={'close': close, 'bb_lower': bb_lower}
        ))

    # 接近上軌 (80% 以上)
    if bb_upper * 0.95 <= close <= bb_upper:
        signals.append(IndicatorSignal(
            indicator='BB',
            signal_type='neutral',
            name='BB 接近上軌',
            confidence=0.45,
            metadata={'close': close, 'bb_upper': bb_upper}
        ))

    # 接近下軌 (80% 以內)
    if bb_lower <= close <= bb_lower * 1.05:
        signals.append(IndicatorSignal(
            indicator='BB',
            signal_type='neutral',
            name='BB 接近下軌',
            confidence=0.45,
            metadata={'close': close, 'bb_lower': bb_lower}
        ))

    # ── ✨ 新增：BB 趨勢信號 ──

    # 價格在 BB 中軌上方（多頭主導）
    if close > bb_middle:
        signals.append(IndicatorSignal(
            indicator='BB',
            signal_type='bullish',
            name='價格在 BB 中軌上方',
            confidence=0.50,
            metadata={'close': close, 'bb_middle': bb_middle}
        ))

    # BB 中軌向上（中軌 SMA 斜率 > 0）
    if idx >= 5:
        bb_middle_5ago = df['bb_middle'].iloc[idx - 5]
        if not pd.isna(bb_middle_5ago) and bb_middle > bb_middle_5ago * 1.005:
            signals.append(IndicatorSignal(
                indicator='BB',
                signal_type='bullish',
                name='BB 中軌向上（上升趨勢）',
                confidence=0.60,
                metadata={'bb_middle': bb_middle, 'bb_middle_5ago': bb_middle_5ago,
                          'slope': (bb_middle / bb_middle_5ago - 1) * 100}
            ))

    return signals


def detect_price_breakout_signals(df: pd.DataFrame, idx: int) -> List[IndicatorSignal]:
    """
    ✨ 新增：價格突破信號（動能版）
    - 價格創 N 日新高：短期/中期突破
    """
    signals = []

    if idx < 60:
        return signals

    close = df['close'].iloc[idx]
    high_20d = df['high_20d'].iloc[idx]
    high_60d = df['high_60d'].iloc[idx]

    if pd.isna(high_20d) or pd.isna(high_60d):
        return signals

    # 價格創 20 日新高
    if close >= high_20d * 0.995:
        signals.append(IndicatorSignal(
            indicator='PRICE',
            signal_type='bullish',
            name='價格創 20 日新高',
            confidence=0.70,
            metadata={'close': close, 'high_20d': high_20d,
                      'pct_off_high': (close / high_20d - 1) * 100}
        ))

    # 價格創 60 日新高（更強的信號）
    if close >= high_60d * 0.995:
        signals.append(IndicatorSignal(
            indicator='PRICE',
            signal_type='bullish',
            name='價格創 60 日新高',
            confidence=0.80,
            metadata={'close': close, 'high_60d': high_60d,
                      'pct_off_high': (close / high_60d - 1) * 100}
        ))

    return signals


def detect_volume_signals(df: pd.DataFrame, idx: int) -> List[IndicatorSignal]:
    """
    ✨ 新增：成交量信號（動能版）
    - 成交量配合上升：上升時有量，健康趨勢
    - 縮量回調：回調時縮量，buy the dip 信號
    """
    signals = []

    if idx < 25:
        return signals

    close = df['close'].iloc[idx]
    close_5ago = df['close'].iloc[idx - 5] if idx >= 5 else close
    volume = df['volume'].iloc[idx]
    vol_ma5 = df['vol_ma5'].iloc[idx]
    vol_ma20 = df['vol_ma20'].iloc[idx]

    if pd.isna(vol_ma5) or pd.isna(vol_ma20) or vol_ma20 == 0:
        return signals

    # 成交量配合上升：5 天均量 > 20 天均量，且價格上升
    if vol_ma5 > vol_ma20 * 1.2 and close > close_5ago:
        signals.append(IndicatorSignal(
            indicator='VOLUME',
            signal_type='bullish',
            name='成交量配合上升（放量上漲）',
            confidence=0.65,
            metadata={'vol_ma5': vol_ma5, 'vol_ma20': vol_ma20,
                      'ratio': vol_ma5 / vol_ma20}
        ))

    # 縮量回調：價格下跌但成交量萎縮（健康回調）
    close_1ago = df['close'].iloc[idx - 1] if idx >= 1 else close
    if close < close_1ago and volume < vol_ma20 * 0.8:
        signals.append(IndicatorSignal(
            indicator='VOLUME',
            signal_type='bullish',
            name='縮量回調（買點信號）',
            confidence=0.55,
            metadata={'volume': volume, 'vol_ma20': vol_ma20,
                      'ratio': volume / vol_ma20}
        ))

    # 放量突破：成交量 > 20 天均量 1.5 倍，且價格上升
    close_1ago = df['close'].iloc[idx - 1] if idx >= 1 else close
    if volume > vol_ma20 * 1.5 and close > close_1ago:
        signals.append(IndicatorSignal(
            indicator='VOLUME',
            signal_type='bullish',
            name='放量突破（強勢確認）',
            confidence=0.70,
            metadata={'volume': volume, 'vol_ma20': vol_ma20,
                      'ratio': volume / vol_ma20}
        ))

    return signals


def detect_adx_signals(df: pd.DataFrame, idx: int) -> List[IndicatorSignal]:
    """
    ✨ 新增：ADX 趨勢強度信號
    - ADX > 25: 強趨勢
    - +DI > -DI: 多頭主導
    - ADX > 25 且 +DI > -DI: 強上升趨勢
    """
    signals = []

    if idx < 20:
        return signals

    adx = df['adx'].iloc[idx]
    plus_di = df['plus_di'].iloc[idx]
    minus_di = df['minus_di'].iloc[idx]

    if pd.isna(adx) or pd.isna(plus_di) or pd.isna(minus_di):
        return signals

    # ADX > 25：強趨勢
    if adx > 25:
        # +DI > -DI：多頭主導
        if plus_di > minus_di:
            signals.append(IndicatorSignal(
                indicator='ADX',
                signal_type='bullish',
                name='ADX 強趨勢（多頭主導）',
                confidence=0.70,
                metadata={'adx': adx, 'plus_di': plus_di, 'minus_di': minus_di}
            ))
        else:
            signals.append(IndicatorSignal(
                indicator='ADX',
                signal_type='bearish',
                name='ADX 強趨勢（空頭主導）',
                confidence=0.70,
                metadata={'adx': adx, 'plus_di': plus_di, 'minus_di': minus_di}
            ))

    # ADX > 40：極強趨勢
    if adx > 40 and plus_di > minus_di:
        signals.append(IndicatorSignal(
            indicator='ADX',
            signal_type='bullish',
            name='ADX 極強趨勢（多頭）',
            confidence=0.80,
            metadata={'adx': adx, 'plus_di': plus_di, 'minus_di': minus_di}
        ))

    return signals


def detect_momentum_summary_signals(df: pd.DataFrame, idx: int) -> List[IndicatorSignal]:
    """
    ✨ 新增：動能綜合信號
    將多個動能信號的結果匯總成一個綜合判斷
    """
    signals = []

    # EMA 多頭排列：已由 detect_ema_signals 處理
    # 這裡檢查價格是否在所有主要均線上方（最強動能確認）
    if idx >= 50:
        close = df['close'].iloc[idx]
        ema_20 = df['ema_20'].iloc[idx]
        ema_50 = df['ema_50'].iloc[idx]
        ema_200 = df['ema_200'].iloc[idx]

        if (close > ema_20 > ema_50 > ema_200 and
            not pd.isna(ema_20) and not pd.isna(ema_50) and not pd.isna(ema_200)):
            signals.append(IndicatorSignal(
                indicator='MOMENTUM',
                signal_type='bullish',
                name='多頭排列 + 價格在均線上方（強勢確認）',
                confidence=0.80,
                metadata={'close': close, 'ema_20': ema_20, 'ema_50': ema_50, 'ema_200': ema_200}
            ))

    return signals


def detect_all_signals(df: pd.DataFrame, idx: int) -> List[IndicatorSignal]:
    """
    整合所有指標信號
    """
    all_signals = []

    all_signals.extend(detect_rsi_signals(df, idx))
    all_signals.extend(detect_macd_signals(df, idx))
    all_signals.extend(detect_kdj_signals(df, idx))
    all_signals.extend(detect_ema_signals(df, idx))
    all_signals.extend(detect_bb_signals(df, idx))
    # ✨ 新增動能信號
    all_signals.extend(detect_price_breakout_signals(df, idx))
    all_signals.extend(detect_volume_signals(df, idx))
    all_signals.extend(detect_adx_signals(df, idx))
    all_signals.extend(detect_momentum_summary_signals(df, idx))

    return all_signals


def filter_strong_signals(signals: List[IndicatorSignal],
                          min_confidence: float = 0.60) -> List[IndicatorSignal]:
    """過濾出高信心度信號"""
    return [s for s in signals if s.confidence >= min_confidence]


def get_dominant_signal(signals: List[IndicatorSignal]) -> Optional[IndicatorSignal]:
    """取得主導信號（信心度最高）"""
    if not signals:
        return None
    return max(signals, key=lambda s: s.confidence)
