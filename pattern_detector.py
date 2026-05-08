"""
pattern_detector.py — 圖表形態識別核心模組
=============================================
形態類型：
  1. 燭線形態（單根/兩根/三根 K 線）
  2. 價格形態（頭肩頂/底、雙頂/底、三角形、旗形、矩形、楔形）
  3. 趨勢信號（支撐/壓力位、均線排列、成交量突破）

返回格式：
  Pattern(name, indices, direction, confidence, metadata)
  - name: 形態名稱
  - indices: 涉及的 K 線索引列表
  - direction: 'bullish' | 'bearish' | 'neutral'
  - confidence: 0.0 ~ 1.0
  - metadata: dict（含詳細說明）
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
import numpy as np
import pandas as pd


# ─────────────────────────────────────────────
# 資料結構
# ─────────────────────────────────────────────

@dataclass
class Pattern:
    name: str
    indices: List[int]          # 涉及的 K 線索引
    direction: str               # 'bullish' | 'bearish' | 'neutral'
    confidence: float            # 0.0 ~ 1.0
    metadata: dict = field(default_factory=dict)

    def label(self) -> str:
        emoji = "🟢" if self.direction == "bullish" else ("🔴" if self.direction == "bearish" else "🟡")
        return f"{emoji} {self.name}"


# ─────────────────────────────────────────────
# 輔助工具
# ─────────────────────────────────────────────

def _to_f(v) -> float:
    """Convert Decimal/float/int to float safely."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _body_size(row) -> float:
    return abs(_to_f(row["close"]) - _to_f(row["open"]))


def _upper_shadow(row) -> float:
    return _to_f(row["high"]) - max(_to_f(row["open"]), _to_f(row["close"]))


def _lower_shadow(row) -> float:
    return min(_to_f(row["open"]), _to_f(row["close"])) - _to_f(row["low"])


def _is_bullish(row) -> bool:
    return _to_f(row["close"]) > _to_f(row["open"])


def _is_bearish(row) -> bool:
    return _to_f(row["close"]) < _to_f(row["open"])


def _range(row) -> float:
    return _to_f(row["high"]) - _to_f(row["low"])


def _avg_range(df, n=20) -> float:
    """最近 n 根的平均 True Range"""
    return df["high"].tail(n).sub(df["low"].tail(n)).mean()


# ─────────────────────────────────────────────
# ① 燭線形態（單根 / 兩根 / 三根 K 線）
# ─────────────────────────────────────────────

def detect_doji(df: pd.DataFrame, idx: int, threshold: float = 0.1) -> Optional[Pattern]:
    """
    十字星：開盤≈收盤（實體很小），上下影線足夠長
    threshold: 實體 / 總range 的最大比例
    """
    row = df.iloc[idx]
    r = _range(row)
    if r == 0:
        return None
    body = _body_size(row)
    upper = _upper_shadow(row)
    lower = _lower_shadow(row)
    if body / r < threshold and upper > 0 and lower > 0:
        # Tiny-body Doji gets boosted confidence so it survives deduplication
        # (body/r < 0.05 means almost-perfect Doji, not just low-confidence)
        conf = round(body / r, 2)
        if conf < 0.05:
            conf = 0.85
        return Pattern(
            name="Doji",
            indices=[idx],
            direction="neutral",
            confidence=conf,
            metadata={"meaning": "十字星 — 多空拉鋸，可能反轉或持續", "idx": idx}
        )
    return None


def detect_hammer(df: pd.DataFrame, idx: int, lookback_trend: int = 10) -> Optional[Pattern]:
    """
    錘子：下影線 ≥ 2×實體，實體靠近頂部，處於下跌趨勢末段
    """
    row = df.iloc[idx]
    if not _is_bullish(row):
        return None
    body = _body_size(row)
    lower = _lower_shadow(row)
    if body < 0.001:  # 很小的實體（近似十字）
        return None
    # 必須下影線足夠長
    if lower < 2 * body:
        return None
    # 確認前面是下跌趨勢
    if idx < lookback_trend:
        return None
    lookback = df.iloc[idx - lookback_trend:idx]
    if lookback["close"].iloc[-1] <= lookback["close"].iloc[0]:
        # 仍在下跌
        return Pattern(
            name="Hammer",
            indices=[idx],
            direction="bullish",
            confidence=round(min(lower / body / 3, 1.0), 2),
            metadata={"meaning": "錘子 — 下跌底部反轉信號", "idx": idx}
        )
    return None


def detect_shooting_star(df: pd.DataFrame, idx: int) -> Optional[Pattern]:
    """
    流星：上影線很長，實體小，處於上升趨勢頂部
    """
    row = df.iloc[idx]
    if not _is_bearish(row):
        return None
    body = _body_size(row)
    upper = _upper_shadow(row)
    lower = _lower_shadow(row)
    if body < 0.001:
        return None
    if upper < 2 * body:
        return None
    # 流星下影線必須極短（< 上影線的 1/3）；否則是吊頸錘或其他形態
    if lower >= upper / 3:
        return None
    if idx < 10:
        return None
    lookback = df.iloc[idx - 10:idx]
    if lookback["close"].iloc[-1] >= lookback["close"].iloc[0]:
        upper_ratio = round(upper / body, 1) if body >= 0.001 else 0.0
        lower_ratio = round(lower / upper, 1) if upper > 0 else 0.0
        return Pattern(
            name="Shooting Star",
            indices=[idx],
            direction="bearish",
            confidence=round(min(upper / body / 3, 1.0), 2),
            metadata={
                "meaning": "流星 — 上升頂部反轉信號",
                "idx": idx,
                "upper_shadow_ratio": upper_ratio,
                "lower_shadow_ratio": lower_ratio,
            }
        )
    return None


def detect_engulfing(df: pd.DataFrame, idx: int) -> Optional[Pattern]:
    """
    吞噬形態：第二根完全覆蓋第一根實體
    """
    if idx < 1:
        return None
    prev = df.iloc[idx - 1]
    curr = df.iloc[idx]
    body_prev = _body_size(prev)
    body_curr = _body_size(curr)
    if body_prev < 0.001 or body_curr < 0.001:
        return None

    # 看漲吞噬：陰包陽
    if _is_bearish(prev) and _is_bullish(curr):
        if _to_f(curr["open"]) < _to_f(prev["close"]) and _to_f(curr["close"]) > _to_f(prev["open"]):
            if idx >= 5:
                lookback = df.iloc[idx - 5:idx - 1]
                if _to_f(lookback["close"].iloc[-1]) < _to_f(lookback["close"].iloc[0]):
                    return Pattern(
                        name="Bullish Engulfing",
                        indices=[idx - 1, idx],
                        direction="bullish",
                        confidence=0.75,
                        metadata={"meaning": "看漲吞噬 — 下降後陽線完全覆蓋陰線", "idx": idx}
                    )

    # 看跌吞噬：陽包陰
    if _is_bullish(prev) and _is_bearish(curr):
        if _to_f(curr["open"]) > _to_f(prev["close"]) and _to_f(curr["close"]) < _to_f(prev["open"]):
            if idx >= 5:
                lookback = df.iloc[idx - 5:idx - 1]
                if _to_f(lookback["close"].iloc[-1]) > _to_f(lookback["close"].iloc[0]):
                    return Pattern(
                        name="Bearish Engulfing",
                        indices=[idx - 1, idx],
                        direction="bearish",
                        confidence=0.75,
                        metadata={"meaning": "看跌吞噬 — 上升後陰線完全覆蓋陽線", "idx": idx}
                    )
    return None


def detect_morning_star(df: pd.DataFrame, idx: int) -> Optional[Pattern]:
    """
    早晨之星：三根 K 線
    ① 第一根：長陰（跌）
    ② 第二根：星形（實體小，可跳空）
    ③ 第三根：長陽（漲），收盤深入第一根實體
    """
    if idx < 2:
        return None
    r1, r2, r3 = df.iloc[idx - 2], df.iloc[idx - 1], df.iloc[idx]
    body1 = _body_size(r1)
    body2 = _body_size(r2)
    body3 = _body_size(r3)
    if body1 < 0.001 or body2 < 0.001 or body3 < 0.001:
        return None
    # r1 長陰，r3 長陽，r2 是星形
    # r1 必须是阴线（第一根下跌）
    if not _is_bearish(r1):
        return None
    # r3 必须是阳线（第三根上涨）
    if not _is_bullish(r3):
        return None
    # r2 是星形（小實體：body2 < body1*0.5 且 body/r < 25%）
    if body2 >= body1 * 0.5:
        return None
    r2_range = _to_f(r2["high"]) - _to_f(r2["low"])
    if r2_range > 0 and body2 / r2_range >= 0.25:
        return None
    # r3 实体要够大（至少是 r1 的一半）
    if body3 < body1 * 0.5:
        return None
    # r3 收盘要深入 r1 实体内部（至少 50%）
    deep = _to_f(r3["close"]) > _to_f(r1["open"]) - 0.5 * body1
    if not deep:
        return None
    return Pattern(
        name="Morning Star",
        indices=[idx - 2, idx - 1, idx],
        direction="bullish",
        confidence=0.8,
        metadata={"meaning": "早晨之星 — 下跌底部三根K線反轉", "idx": idx}
    )


def detect_evening_star(df: pd.DataFrame, idx: int) -> Optional[Pattern]:
    """
    黃昏之星：三根 K 線（早晨之星的空頭版本）
    """
    if idx < 2:
        return None
    r1, r2, r3 = df.iloc[idx - 2], df.iloc[idx - 1], df.iloc[idx]
    body1 = _body_size(r1)
    body2 = _body_size(r2)
    body3 = _body_size(r3)
    if body1 < 0.001 or body2 < 0.001 or body3 < 0.001:
        return None
    # r1 必须是阳线（第一根上涨）
    if not _is_bullish(r1):
        return None
    # r3 必须是阴线（第三根下跌）
    if not _is_bearish(r3):
        return None
    # r2 是星形（小實體：body2 < body1*0.5 且 body/r < 25%）
    if body2 >= body1 * 0.5:
        return None
    r2_range = _to_f(r2["high"]) - _to_f(r2["low"])
    if r2_range > 0 and body2 / r2_range >= 0.25:
        return None
    # r3 实体要够大（至少是 r1 的一半）
    if body3 < body1 * 0.5:
        return None
    # r3 收盘要深入 r1 实体内部（至少 50%）
    deep = _to_f(r3["close"]) < _to_f(r1["open"]) + 0.5 * body1
    if not deep:
        return None
    return Pattern(
        name="Evening Star",
        indices=[idx - 2, idx - 1, idx],
        direction="bearish",
        confidence=0.8,
        metadata={"meaning": "黃昏之星 — 上升頂部三根K線反轉", "idx": idx}
    )
    return None


def detect_harami(df: pd.DataFrame, idx: int) -> Optional[Pattern]:
    """
    Harami（內含線）：第二根完全被第一根實體包含
    """
    if idx < 1:
        return None
    r1, r2 = df.iloc[idx - 1], df.iloc[idx]
    body1 = _body_size(r1)
    body2 = _body_size(r2)
    if body1 < 0.001 or body2 < 0.001:
        return None
    # Harami 要求 r2 實體明顯小於 r1（< r1 的 50%），否則只是普通包含關係
    if body2 >= body1 * 0.5:
        return None
    # r2 的實體完全在 r1 實體範圍內（只看實體，不看影線）
    if (min(_to_f(r2["open"]), _to_f(r2["close"])) > min(_to_f(r1["open"]), _to_f(r1["close"])) and
        max(_to_f(r2["open"]), _to_f(r2["close"])) < max(_to_f(r1["open"]), _to_f(r1["close"]))):
        # Harami: direction 由 r2（內含的那根）決定
        # r2 陽線覆蓋 r1 → bullish reversal；r2 陰線覆蓋 r1 → bearish reversal
        direction = "bullish" if _is_bullish(r2) else "bearish" if _is_bearish(r2) else "neutral"
        name = "Bullish Harami" if direction == "bullish" else "Bearish Harami"
        return Pattern(
            name=name,
            indices=[idx - 1, idx],
            direction=direction,
            confidence=0.65,
            metadata={"meaning": f"{name} — 第二根被第一根完全包含", "idx": idx}
        )
    return None


# ─────────────────────────────────────────────
# ② 價格形態（多根 K 線）
# ─────────────────────────────────────────────

def _detect_swing_points(df: pd.DataFrame) -> Tuple[List[int], List[int]]:
    """
    找出擺動高點和低點（局部極值）
    返回 (swing_highs, swing_lows) — 各索引列表
    """
    n = len(df)
    highs, lows = [], []
    for i in range(2, n - 2):
        if df["high"].iloc[i] > df["high"].iloc[i - 1] and df["high"].iloc[i] > df["high"].iloc[i - 2] and \
           df["high"].iloc[i] > df["high"].iloc[i + 1] and df["high"].iloc[i] > df["high"].iloc[i + 2]:
            highs.append(i)
        if df["low"].iloc[i] < df["low"].iloc[i - 1] and df["low"].iloc[i] < df["low"].iloc[i - 2] and \
           df["low"].iloc[i] < df["low"].iloc[i + 1] and df["low"].iloc[i] < df["low"].iloc[i + 2]:
            lows.append(i)
    return highs, lows


def detect_head_shoulders(df: pd.DataFrame) -> List[Pattern]:
    """
    頭肩頂/底：檢測三個峰/谷，中間最高/最低，價格觸碰頸線後突破
    简化版：找三個相鄰的擺動高點（頭肩頂）或低點（頭肩底）
    """
    patterns = []
    highs, lows = _detect_swing_points(df)
    n = len(df)

    # 頭肩頂：三個相鄰的高點，中間最高，且兩肩高度相近
    if len(highs) >= 3:
        for i in range(len(highs) - 2):
            h1, h2, h3 = highs[i], highs[i + 1], highs[i + 2]
            if h3 - h1 < n * 0.5:  # 三峰不要太散
                p1, p2, p3 = df["high"].iloc[h1], df["high"].iloc[h2], df["high"].iloc[h3]
                # 中間最高（頭），兩肩差不多高
                if p2 > p1 and p2 > p3 and abs(p1 - p3) / p2 < 0.05:
                    # 頸線：兩肩的低點
                    neck = min(df["low"].iloc[h1], df["low"].iloc[h3])
                    # 最後價格跌破頸線
                    last_close = df["close"].iloc[-1]
                    if last_close < neck:
                        patterns.append(Pattern(
                            name="Head & Shoulders",
                            indices=[h1, h2, h3],
                            direction="bearish",
                            confidence=0.75,
                            metadata={"meaning": "頭肩頂 — 看跌反轉形態", "neckline": neck, "idx": h2}
                        ))

    # 頭肩底（倒頭肩）
    if len(lows) >= 3:
        for i in range(len(lows) - 2):
            l1, l2, l3 = lows[i], lows[i + 1], lows[i + 2]
            if l3 - l1 < n * 0.5:
                v1, v2, v3 = df["low"].iloc[l1], df["low"].iloc[l2], df["low"].iloc[l3]
                if v2 < v1 and v2 < v3 and abs(v1 - v3) / v2 < 0.05:
                    neck = max(df["high"].iloc[l1], df["high"].iloc[l3])
                    last_close = df["close"].iloc[-1]
                    if last_close > neck:
                        patterns.append(Pattern(
                            name="Inverse H&S",
                            indices=[l1, l2, l3],
                            direction="bullish",
                            confidence=0.75,
                            metadata={"meaning": "倒頭肩底 — 看漲反轉形態", "neckline": neck, "idx": l2}
                        ))
    return patterns


def detect_double_top_bottom(df: pd.DataFrame) -> List[Pattern]:
    """
    雙頂/雙底：兩個相近的高點/低點，頸線突破確認
    """
    patterns = []
    highs, lows = _detect_swing_points(df)
    n = len(df)
    avg_r = _avg_range(df)

    # 雙頂
    if len(highs) >= 2:
        for i in range(len(highs) - 1):
            h1, h2 = highs[i], highs[i + 1]
            if h2 - h1 < n * 0.4:
                p1, p2 = df["high"].iloc[h1], df["high"].iloc[h2]
                if abs(p1 - p2) < 2 * avg_r:
                    neck = min(df["low"].iloc[h1], df["low"].iloc[h2])
                    last_close = df["close"].iloc[-1]
                    if last_close < neck:
                        patterns.append(Pattern(
                            name="Double Top",
                            indices=[h1, h2],
                            direction="bearish",
                            confidence=0.7,
                            metadata={"meaning": "雙頂 — M型看跌形態", "neckline": neck, "idx": h2}
                        ))

    # 雙底
    if len(lows) >= 2:
        for i in range(len(lows) - 1):
            l1, l2 = lows[i], lows[i + 1]
            if l2 - l1 < n * 0.4:
                v1, v2 = df["low"].iloc[l1], df["low"].iloc[l2]
                if abs(v1 - v2) < 2 * avg_r:
                    neck = max(df["high"].iloc[l1], df["high"].iloc[l2])
                    last_close = df["close"].iloc[-1]
                    if last_close > neck:
                        patterns.append(Pattern(
                            name="Double Bottom",
                            indices=[l1, l2],
                            direction="bullish",
                            confidence=0.7,
                            metadata={"meaning": "雙底 — W型看漲形態", "neckline": neck, "idx": l2}
                        ))
    return patterns


def detect_triangle(df: pd.DataFrame) -> List[Pattern]:
    """
    三角形：利用線性回歸擬合高點（下降）和低點（上升），檢測收斂
    简化版：比較前1/3和後1/3的價格範圍是否顯著收斂
    """
    patterns = []
    n = len(df)
    if n < 30:
        return patterns

    seg = n // 3
    early_high = df["high"].iloc[:seg].max()
    late_high = df["high"].iloc[-seg:].max()
    early_low = df["low"].iloc[:seg].min()
    late_low = df["low"].iloc[-seg:].min()

    range_early = early_high - early_low
    range_late = late_high - late_low

    # 顯著收斂
    if range_early > 1.5 * range_late and range_late > 0:
        # 判斷方向：上升三角形（低點上升）vs 下降三角形（高點下降）
        if late_low > early_low + 0.5 * range_early:
            direction = "bullish"
            name = "Ascending Triangle"
            meaning = "上升三角形 — 往上突破預期"
        elif late_high < early_high - 0.5 * range_early:
            direction = "bearish"
            name = "Descending Triangle"
            meaning = "下降三角形 — 往下突破預期"
        else:
            direction = "neutral"
            name = "Symmetrical Triangle"
            meaning = "對稱三角形 — 等待突破方向確認"

        mid_idx = n // 2
        patterns.append(Pattern(
            name=name,
            indices=[seg // 2, n - seg // 2],
            direction=direction,
            confidence=0.65,
            metadata={"meaning": meaning, "idx": mid_idx}
        ))
    return patterns


def detect_flag(df: pd.DataFrame) -> List[Pattern]:
    """
    旗形：急劇上升/下降後的短暫盤整（略微反向）
    檢測：前面有一段≥15%的急漲/急跌，後面盤整8-12根K線，價格略微回調
    """
    patterns = []
    n = len(df)
    if n < 25:
        return patterns

    # 檢查最近一段是否為顯著趨勢
    pole_size = df["close"].iloc[-1] - df["close"].iloc[-20]
    pole_pct = pole_size / df["close"].iloc[-20]
    if abs(pole_pct) < 0.08:
        return patterns  # pole 不夠陡

    # 旗杆後8-12根
    for flag_len in range(8, 13):
        if n - 20 - flag_len < 0:
            continue
        pole_end = n - 20
        flag_start = pole_end
        flag_end = pole_end + flag_len
        pole_range = df["high"].iloc[1:pole_end].max() - df["low"].iloc[1:pole_end].min()

        flag_segment = df.iloc[flag_start:flag_end]
        flag_range = flag_segment["high"].max() - flag_segment["low"].min()

        # 旗桿和旗面的比例
        if flag_range < 0.4 * pole_range:
            direction = "bullish" if pole_pct > 0 else "bearish"
            name = "Bull Flag" if direction == "bullish" else "Bear Flag"
            patterns.append(Pattern(
                name=name,
                indices=[flag_start, flag_end - 1],
                direction=direction,
                confidence=0.6,
                metadata={"meaning": f"{name} — 持續形態", "idx": (flag_start + flag_end) // 2}
            ))
            break
    return patterns


def detect_support_resistance(df: pd.DataFrame, tolerance: float = 0.02) -> List[Pattern]:
    """
    支撐位/壓力位：股價多次觸及的價格水平
    tolerance: 價格水平在多少%範圍內視為同一水平
    """
    patterns = []
    highs, lows = _detect_swing_points(df)

    def cluster_levels(indices, price_func, name, direction):
        if len(indices) < 2:
            return
        levels = []
        for idx in indices:
            price = price_func(df.iloc[idx])
            merged = False
            for i, (lvl, _) in enumerate(levels):
                if abs(price - lvl) / lvl < tolerance:
                    levels[i] = (lvl, levels[i][1] + 1)
                    merged = True
                    break
            if not merged:
                levels.append((price, 1))
        # 只留觸及≥2次
        for lvl, count in levels:
            if count >= 2:
                idx = max(indices, key=lambda x: abs(price_func(df.iloc[x]) - lvl))
                patterns.append(Pattern(
                    name=name,
                    indices=[idx],
                    direction=direction,
                    confidence=min(0.5 + count * 0.1, 0.9),
                    metadata={"meaning": f"{name} @ {lvl:.2f}（{count}次觸及）", "level": lvl, "idx": idx}
                ))

    cluster_levels(lows, lambda r: r["low"], "Support", "bullish")
    cluster_levels(highs, lambda r: r["high"], "Resistance", "bearish")
    return patterns


def detect_ma_alignment(df: pd.DataFrame, short=5, mid=20, long=60) -> List[Pattern]:
    """
    均線多頭/空頭排列
    """
    patterns = []
    if len(df) < long:
        return patterns
    df = df.copy()
    df[f"ma{short}"] = df["close"].rolling(short).mean()
    df[f"ma{mid}"] = df["close"].rolling(mid).mean()
    df[f"ma{long}"] = df["close"].rolling(long).mean()
    last = df.iloc[-1]
    if pd.isna(last[f"ma{short}"]) or pd.isna(last[f"ma{long}"]):
        return patterns

    s, m, l = last[f"ma{short}"], last[f"ma{mid}"], last[f"ma{long}"]
    price = last["close"]

    # 多頭排列：ma5 > ma20 > ma60 且價格在所有均線之上
    if s > m > l and price > s:
        patterns.append(Pattern(
            name="MA Bullish Alignment",
            indices=[len(df) - 1],
            direction="bullish",
            confidence=0.75,
            metadata={"meaning": "均線多頭排列 — 短/中/長均線向上，價格在所有均線之上", "idx": len(df) - 1}
        ))
    # 空頭排列
    elif s < m < l and price < s:
        patterns.append(Pattern(
            name="MA Bearish Alignment",
            indices=[len(df) - 1],
            direction="bearish",
            confidence=0.75,
            metadata={"meaning": "均線空頭排列 — 短/中/長均線向下，價格在所有均線之下", "idx": len(df) - 1}
        ))
    return patterns


def detect_volume_breakout(df: pd.DataFrame, lookback=20) -> List[Pattern]:
    """
    成交量突破：價格創 N 日新高/新低，同時成交量顯著放大（> 1.5x 平均）
    """
    patterns = []
    if len(df) < lookback + 1:
        return patterns
    avg_vol = df["volume"].tail(lookback).mean()
    last_vol = df["volume"].iloc[-1]
    last_high = df["high"].iloc[-1]
    last_low = df["low"].iloc[-1]
    n = len(df)

    # 價格創 N 日新高 + 放量
    if last_vol > 1.5 * avg_vol:
        if last_high == df["high"].tail(lookback).max():
            patterns.append(Pattern(
                name="Volume + Price Breakout",
                indices=[n - 1],
                direction="bullish",
                confidence=0.7,
                metadata={"meaning": "放量突破 — 成交量放大且創新高", "idx": n - 1, "vol_ratio": round(last_vol / avg_vol, 2)}
            ))
        elif last_low == df["low"].tail(lookback).min():
            patterns.append(Pattern(
                name="Volume + Price Breakdown",
                indices=[n - 1],
                direction="bearish",
                confidence=0.7,
                metadata={"meaning": "放量破底 — 成交量放大且創新低", "idx": n - 1, "vol_ratio": round(last_vol / avg_vol, 2)}
            ))
    return patterns


# ─────────────────────────────────────────────
# 主調度函數
# ─────────────────────────────────────────────

def detect_all_patterns(df: pd.DataFrame) -> List[Pattern]:
    """
    掃描整個 DataFrame，回傳所有識別到的形態
    """
    if df is None or len(df) < 5:
        return []

    results: List[Pattern] = []

    # ① 燭線形態（逐根掃描）
    # 順序重要：精確的單根形態優先於組合形態，避免被 engulfing 搶走 index
    for idx in range(2, len(df)):
        for detector in [
            lambda i, _df=df, _d0=detect_doji:    _d0(_df, i),
            lambda i, _df=df, _d0=detect_hammer:   _d0(_df, i),
            lambda i, _df=df, _d0=detect_shooting_star: _d0(_df, i),
            lambda i, _df=df, _d0=detect_morning_star:  _d0(_df, i),
            lambda i, _df=df, _d0=detect_evening_star:  _d0(_df, i),
            lambda i, _df=df, _d0=detect_engulfing:    _d0(_df, i),
            lambda i, _df=df, _d0=detect_harami:        _d0(_df, i),
        ]:
            p = detector(idx)
            if p:
                results.append(p)

    # ② 價格形態（整體分析）
    for detector in [
        detect_head_shoulders,
        detect_double_top_bottom,
        detect_triangle,
        detect_flag,
        detect_support_resistance,
    ]:
        try:
            results.extend(detector(df))
        except Exception:
            pass

    # ③ 趨勢信號
    results.extend(detect_ma_alignment(df))
    results.extend(detect_volume_breakout(df))

    # 去重（同一 index 只留最高置信度）
    # Special case: when Doji and Shooting Star share the same index, Shooting Star
    # wins if upper shadow is disproportionately large (> 5x body), because a massive
    # upper shadow means the candle is structurally a流星, not a十字星.
    # Doji wins only when it is a "pure" Doji (no single shadow dominating).
    seen: dict = {}
    multi_at: dict = {}  # idx → list of non-Doji patterns at this index

    # Pass 1: separate Doji from everything else
    doji_at: dict = {}   # idx → Doji pattern
    for p in results:
        if p.name == "Doji":
            for idx in p.indices:
                if idx not in doji_at or p.confidence > doji_at[idx].confidence:
                    doji_at[idx] = p
        else:
            for idx in p.indices:
                if idx not in multi_at:
                    multi_at[idx] = []
                multi_at[idx].append(p)

    # Pass 2: resolve conflicts — prefer the more specific pattern
    # (iterate over a snapshot of keys since we may delete from doji_at)
    for idx in list(doji_at.keys()):
        if idx in multi_at:
            doji = doji_at[idx]
            for p in multi_at[idx]:
                # Shooting Star / Inverted Hammer with huge upper shadow beats Doji
                if p.name in ("Shooting Star", "Inverted Hammer"):
                    # Extract upper shadow ratio from metadata if available
                    upper_ratio = p.metadata.get("upper_shadow_ratio", 0) if p.metadata else 0
                    if upper_ratio > 5:
                        del doji_at[idx]  # demote Doji — Shooting Star wins
                        break

    # Pass 3: build final list, Doji blocks less-confident peers at same index
    for p in sorted(results, key=lambda x: -x.confidence):
        for idx in p.indices:
            if idx in doji_at and p.name != "Doji":
                continue  # skip: a (pure) Doji occupies this index
            if idx not in seen:
                seen[idx] = p
                break

    return sorted(list(seen.values()), key=lambda x: x.metadata.get("idx", 0))


def get_latest_patterns(df: pd.DataFrame, lookback: int = 30) -> List[Pattern]:
    """
    只回傳最近 lookback 根 K 線內的形態（減輕噪音）
    """
    all_p = detect_all_patterns(df)
    recent_idx = set(range(len(df) - lookback, len(df)))
    return [p for p in all_p if any(i in recent_idx for i in p.indices)]
