"""
pattern_detector.py — 圖表形態識別核心模組
=============================================
形態類型：
  1. 燭線形態（單根/兩根/三根/五根 K 線）
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
    # 錘子上影線必須極短（< 下影線的 1/3）；否則是流星倒轉
    upper = _upper_shadow(row)
    if upper >= lower / 3:
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
    # 趨勢確認：早晨之星前需為下降趨勢（前 4 根收盤價遞減）
    if idx >= 6:
        lookback = df.iloc[idx - 6:idx - 2]
        first_close = _to_f(lookback["close"].iloc[0])
        last_close = _to_f(lookback["close"].iloc[-1])
        if not (last_close < first_close):
            return None  # 前 4 根不在下降，不符合早晨之星背景
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
    # 趨勢確認：黃昏之星前需為上升趨勢（前 4 根收盤價遞增）
    if idx >= 6:
        lookback = df.iloc[idx - 6:idx - 2]
        first_close = _to_f(lookback["close"].iloc[0])
        last_close = _to_f(lookback["close"].iloc[-1])
        if not (last_close > first_close):
            return None  # 前 4 根不在上升，不符合黃昏之星背景
    return Pattern(
        name="Evening Star",
        indices=[idx - 2, idx - 1, idx],
        direction="bearish",
        confidence=0.8,
        metadata={"meaning": "黃昏之星 — 上升頂部三根K線反轉", "idx": idx}
    )


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

        # 趨勢確認：Bullish Harami 需要前 4 根為下降；Bearish Harami 需要前 4 根為上升
        if idx >= 5:
            lookback = df.iloc[idx - 5:idx - 1]
            last_close = _to_f(lookback["close"].iloc[-1])
            first_close = _to_f(lookback["close"].iloc[0])
            if direction == "bullish" and not (last_close < first_close):
                return None  # 前 4 根不在下降，不符合看漲反轉背景
            if direction == "bearish" and not (last_close > first_close):
                return None  # 前 4 根不在上升，不符合看跌反轉背景

        return Pattern(
            name=name,
            indices=[idx - 1, idx],
            direction=direction,
            confidence=0.65,
            metadata={"meaning": f"{name} — 見底/見頂反轉信號", "idx": idx}
        )
    return None


def detect_piercing_line(df: pd.DataFrame, idx: int) -> Optional[Pattern]:
    """
    刺透線（Piercing Line）：雙根形態
    ① 第一根：長陰
    ② 第二根：陽線開盤低開（低於昨收），收盤深入陰線實體 50% 以上
    """
    if idx < 1:
        return None
    prev = df.iloc[idx - 1]
    curr = df.iloc[idx]
    body_prev = _body_size(prev)
    body_curr = _body_size(curr)
    if body_prev < 0.001 or body_curr < 0.001:
        return None
    if not _is_bearish(prev) or not _is_bullish(curr):
        return None
    # 陽線開盤低於昨收，且收盤深入陰線實體 50% 以上
    open_curr = _to_f(curr["open"])
    close_curr = _to_f(curr["close"])
    close_prev = _to_f(prev["close"])
    open_prev = _to_f(prev["open"])
    if open_curr >= close_prev:
        return None
    if close_curr < open_prev - 0.5 * body_prev:
        return None
    # 趨勢確認：前 4 根為下降
    if idx >= 5:
        lookback = df.iloc[idx - 5:idx - 1]
        if not (_to_f(lookback["close"].iloc[-1]) < _to_f(lookback["close"].iloc[0])):
            return None
    return Pattern(
        name="Piercing Line",
        indices=[idx - 1, idx],
        direction="bullish",
        confidence=0.72,
        metadata={"meaning": "刺透線 — 下跌後陽線反攻，見底信號", "idx": idx}
    )


def detect_dark_cloud_cover(df: pd.DataFrame, idx: int) -> Optional[Pattern]:
    """
    烏雲蓋頂（Dark Cloud Cover）：雙根形態，空頭版本
    ① 第一根：長陽
    ② 第二根：陰線開盤高開（高於昨收），收盤深入陽線實體 50% 以上
    """
    if idx < 1:
        return None
    prev = df.iloc[idx - 1]
    curr = df.iloc[idx]
    body_prev = _body_size(prev)
    body_curr = _body_size(curr)
    if body_prev < 0.001 or body_curr < 0.001:
        return None
    if not _is_bullish(prev) or not _is_bearish(curr):
        return None
    open_curr = _to_f(curr["open"])
    close_curr = _to_f(curr["close"])
    close_prev = _to_f(prev["close"])
    open_prev = _to_f(prev["open"])
    if open_curr <= close_prev:
        return None
    if close_curr > open_prev - 0.5 * body_prev:
        return None
    # 趨勢確認：前 4 根為上升
    if idx >= 5:
        lookback = df.iloc[idx - 5:idx - 1]
        if not (_to_f(lookback["close"].iloc[-1]) > _to_f(lookback["close"].iloc[0])):
            return None
    return Pattern(
        name="Dark Cloud Cover",
        indices=[idx - 1, idx],
        direction="bearish",
        confidence=0.72,
        metadata={"meaning": "烏雲蓋頂 — 上升後陰線覆蓋，見頂信號", "idx": idx}
    )


def detect_tweezer_bottom(df: pd.DataFrame, idx: int) -> Optional[Pattern]:
    """
    鉗子底（Tweezer Bottom）：雙根形態
    兩根 K 線最低價相同或非常接近（±1%），代表支撐強
    """
    if idx < 1:
        return None
    r1 = df.iloc[idx - 1]
    r2 = df.iloc[idx]
    low1 = _to_f(r1["low"])
    low2 = _to_f(r2["low"])
    if low2 == 0:
        return None
    if abs(low1 - low2) / low2 > 0.01:
        return None
    # 趨勢確認：前 4 根為下降
    if idx >= 5:
        lookback = df.iloc[idx - 5:idx - 1]
        if not (_to_f(lookback["close"].iloc[-1]) < _to_f(lookback["close"].iloc[0])):
            return None
    return Pattern(
        name="Tweezer Bottom",
        indices=[idx - 1, idx],
        direction="bullish",
        confidence=0.68,
        metadata={"meaning": "鉗子底 — 雙底支撐確認，見底信號", "idx": idx}
    )


def detect_tweezer_top(df: pd.DataFrame, idx: int) -> Optional[Pattern]:
    """
    鉗子頂（Tweezer Top）：雙根形態
    兩根 K 線最高價相同或非常接近（±1%），代表壓力強
    """
    if idx < 1:
        return None
    r1 = df.iloc[idx - 1]
    r2 = df.iloc[idx]
    high1 = _to_f(r1["high"])
    high2 = _to_f(r2["high"])
    if high2 == 0:
        return None
    if abs(high1 - high2) / high2 > 0.01:
        return None
    # 趨勢確認：前 4 根為上升
    if idx >= 5:
        lookback = df.iloc[idx - 5:idx - 1]
        if not (_to_f(lookback["close"].iloc[-1]) > _to_f(lookback["close"].iloc[0])):
            return None
    return Pattern(
        name="Tweezer Top",
        indices=[idx - 1, idx],
        direction="bearish",
        confidence=0.68,
        metadata={"meaning": "鉗子頂 — 雙頂壓力確認，見頂信號", "idx": idx}
    )


def detect_three_white_soldiers(df: pd.DataFrame, idx: int) -> Optional[Pattern]:
    """
    三白兵（Three White Soldiers）：三根 K 線
    三根連續陽線，每根收盤價逐步走高，實體大致相同
    """
    if idx < 2:
        return None
    r1, r2, r3 = df.iloc[idx - 2], df.iloc[idx - 1], df.iloc[idx]
    body1 = _body_size(r1)
    body2 = _body_size(r2)
    body3 = _body_size(r3)
    if body1 < 0.001 or body2 < 0.001 or body3 < 0.001:
        return None
    if not (_is_bullish(r1) and _is_bullish(r2) and _is_bullish(r3)):
        return None
    # 三根實體大小相近（±30%）
    avg_body = (body1 + body2 + body3) / 3
    if not (abs(body1 - avg_body) / avg_body < 0.3 and
            abs(body2 - avg_body) / avg_body < 0.3 and
            abs(body3 - avg_body) / avg_body < 0.3):
        return None
    # 每根收盤價高於前一根收盤價
    c1, c2, c3 = _to_f(r1["close"]), _to_f(r2["close"]), _to_f(r3["close"])
    if not (c2 > c1 and c3 > c2):
        return None
    # 每根開盤價在前一根實體上半部分
    if not (_to_f(r2["open"]) > _to_f(r1["close"]) - body1 * 0.5 and
            _to_f(r3["open"]) > _to_f(r2["close"]) - body2 * 0.5):
        return None
    return Pattern(
        name="Three White Soldiers",
        indices=[idx - 2, idx - 1, idx],
        direction="bullish",
        confidence=0.80,
        metadata={"meaning": "三白兵 — 連續三陽強勢上攻，持續看漲", "idx": idx}
    )


def detect_three_black_crows(df: pd.DataFrame, idx: int) -> Optional[Pattern]:
    """
    三黑鴉（Three Black Crows）：三根 K 線
    三根連續陰線，每根收盤價逐步走低，實體大致相同
    """
    if idx < 2:
        return None
    r1, r2, r3 = df.iloc[idx - 2], df.iloc[idx - 1], df.iloc[idx]
    body1 = _body_size(r1)
    body2 = _body_size(r2)
    body3 = _body_size(r3)
    if body1 < 0.001 or body2 < 0.001 or body3 < 0.001:
        return None
    if not (_is_bearish(r1) and _is_bearish(r2) and _is_bearish(r3)):
        return None
    avg_body = (body1 + body2 + body3) / 3
    if not (abs(body1 - avg_body) / avg_body < 0.3 and
            abs(body2 - avg_body) / avg_body < 0.3 and
            abs(body3 - avg_body) / avg_body < 0.3):
        return None
    c1, c2, c3 = _to_f(r1["close"]), _to_f(r2["close"]), _to_f(r3["close"])
    if not (c2 < c1 and c3 < c2):
        return None
    if not (_to_f(r2["open"]) < _to_f(r1["close"]) + body1 * 0.5 and
            _to_f(r3["open"]) < _to_f(r2["close"]) + body2 * 0.5):
        return None
    return Pattern(
        name="Three Black Crows",
        indices=[idx - 2, idx - 1, idx],
        direction="bearish",
        confidence=0.80,
        metadata={"meaning": "三黑鴉 — 連續三陰強勢下跌，持續看跌", "idx": idx}
    )


def detect_three_inside_up(df: pd.DataFrame, idx: int) -> Optional[Pattern]:
    """
    內三向上（Three Inside Up）：三根 K 線
    ① 第一根：長陰
    ② 第二根：陽線，完全在第一根實體範圍內（內含線）
    ③ 第三根：陽線收盤高於第一根收盤
    """
    if idx < 2:
        return None
    r1, r2, r3 = df.iloc[idx - 2], df.iloc[idx - 1], df.iloc[idx]
    body1, body2, body3 = _body_size(r1), _body_size(r2), _body_size(r3)
    if body1 < 0.001 or body2 < 0.001 or body3 < 0.001:
        return None
    if not (_is_bearish(r1) and _is_bullish(r2) and _is_bullish(r3)):
        return None
    # r2 必須完全在 r1 實體範圍內
    if not (min(_to_f(r2["open"]), _to_f(r2["close"])) > min(_to_f(r1["open"]), _to_f(r1["close"])) and
            max(_to_f(r2["open"]), _to_f(r2["close"])) < max(_to_f(r1["open"]), _to_f(r1["close"]))):
        return None
    # r3 收盤高於 r1 收盤（確認上破）
    if _to_f(r3["close"]) <= _to_f(r1["close"]):
        return None
    return Pattern(
        name="Three Inside Up",
        indices=[idx - 2, idx - 1, idx],
        direction="bullish",
        confidence=0.78,
        metadata={"meaning": "內三向上 — 母子突破，持續看漲", "idx": idx}
    )


def detect_three_outside_up(df: pd.DataFrame, idx: int) -> Optional[Pattern]:
    """
    外三向上（Three Outside Up）：三根 K 線
    ① 第一根：長陰
    ② 第二根：陽線完全覆蓋第一根（吞噬）
    ③ 第三根：陽線收盤高於第二根收盤
    """
    if idx < 2:
        return None
    r1, r2, r3 = df.iloc[idx - 2], df.iloc[idx - 1], df.iloc[idx]
    body1, body2, body3 = _body_size(r1), _body_size(r2), _body_size(r3)
    if body1 < 0.001 or body2 < 0.001 or body3 < 0.001:
        return None
    if not (_is_bearish(r1) and _is_bullish(r2) and _is_bullish(r3)):
        return None
    # r2（陽線）必須完全覆蓋 r1（陰線）
    if not (_to_f(r2["open"]) < _to_f(r1["close"]) and _to_f(r2["close"]) > _to_f(r1["open"])):
        return None
    # r3 收盤高於 r2 收盤
    if _to_f(r3["close"]) <= _to_f(r2["close"]):
        return None
    return Pattern(
        name="Three Outside Up",
        indices=[idx - 2, idx - 1, idx],
        direction="bullish",
        confidence=0.80,
        metadata={"meaning": "外三向上 — 吞噬反攻，持續看漲", "idx": idx}
    )


def detect_rising_three_methods(df: pd.DataFrame, idx: int) -> Optional[Pattern]:
    """
    上升三法（Rising Three Methods）：五根 K 線，持續形態
    ① 長陽 → ②③④ 三根小回調（在長陽實體範圍內）→ ⑤ 長陽突破
    """
    if idx < 4:
        return None
    r1, r2, r3, r4, r5 = (df.iloc[idx - 4], df.iloc[idx - 3],
                           df.iloc[idx - 2], df.iloc[idx - 1], df.iloc[idx])
    body1 = _body_size(r1)
    body5 = _body_size(r5)
    if body1 < 0.001 or body5 < 0.001:
        return None
    # r1 和 r5 必須是長陽
    if not (_is_bullish(r1) and _is_bullish(r5)):
        return None
    # r5 收盤必須高於 r1 高點（突破）
    if _to_f(r5["close"]) <= _to_f(r1["high"]):
        return None
    # r2、r3、r4 實體必須在 r1 範圍內
    for r in [r2, r3, r4]:
        r_open = _to_f(r["open"])
        r_close = _to_f(r["close"])
        if not (min(r_open, r_close) >= min(_to_f(r1["open"]), _to_f(r1["close"])) and
                max(r_open, r_close) <= max(_to_f(r1["open"]), _to_f(r1["close"]))):
            return None
    return Pattern(
        name="Rising Three Methods",
        indices=[idx - 4, idx - 3, idx - 2, idx - 1, idx],
        direction="bullish",
        confidence=0.75,
        metadata={"meaning": "上升三法 — 強勢持續形態，持股信號", "idx": idx}
    )


def detect_falling_three_methods(df: pd.DataFrame, idx: int) -> Optional[Pattern]:
    """
    下降三法（Falling Three Methods）：五根 K 線，持續形態
    ① 長陰 → ②③④ 三根小反彈（在長陰實體範圍內）→ ⑤ 長陰跌破
    """
    if idx < 4:
        return None
    r1, r2, r3, r4, r5 = (df.iloc[idx - 4], df.iloc[idx - 3],
                           df.iloc[idx - 2], df.iloc[idx - 1], df.iloc[idx])
    body1 = _body_size(r1)
    body5 = _body_size(r5)
    if body1 < 0.001 or body5 < 0.001:
        return None
    if not (_is_bearish(r1) and _is_bearish(r5)):
        return None
    if _to_f(r5["close"]) >= _to_f(r1["low"]):
        return None
    for r in [r2, r3, r4]:
        r_open = _to_f(r["open"])
        r_close = _to_f(r["close"])
        if not (min(r_open, r_close) >= min(_to_f(r1["open"]), _to_f(r1["close"])) and
                max(r_open, r_close) <= max(_to_f(r1["open"]), _to_f(r1["close"]))):
            return None
    return Pattern(
        name="Falling Three Methods",
        indices=[idx - 4, idx - 3, idx - 2, idx - 1, idx],
        direction="bearish",
        confidence=0.75,
        metadata={"meaning": "下降三法 — 弱勢持續形態，空倉信號", "idx": idx}
    )


def detect_inverted_hammer(df: pd.DataFrame, idx: int) -> Optional[Pattern]:
    """
    倒錘頭（Inverted Hammer）：單根 K 線，看漲逆轉
    上影線很長，實體小，位於下跌趨勢末段
    """
    row = df.iloc[idx]
    if not _is_bearish(row):
        return None
    body = _body_size(row)
    if body < 0.001:
        return None
    upper = _upper_shadow(row)
    lower = _lower_shadow(row)
    if upper < 2 * body:
        return None
    if lower >= upper / 3:
        return None
    if idx < 10:
        return None
    lookback = df.iloc[idx - 10:idx]
    if lookback["close"].iloc[-1] <= lookback["close"].iloc[0]:
        return None
    upper_ratio = round(upper / body, 1) if body >= 0.001 else 0.0
    return Pattern(
        name="Inverted Hammer",
        indices=[idx],
        direction="bullish",
        confidence=0.70,
        metadata={"meaning": "倒錘頭 — 下跌後上影線出貨，注意反轉", "idx": idx, "upper_shadow_ratio": upper_ratio}
    )


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


# ══════════════════════════════════════════════════
# ✨ 新增：延續形態（動能版） 
# ══════════════════════════════════════════════════

def detect_cup_handle(df: pd.DataFrame,
                      min_cup_depth: float = 0.10,
                      max_cup_depth: float = 0.40,
                      handle_retrace: float = 0.3) -> List[Pattern]:
    """
    Cup & Handle（杯柄形態）— 經典延續形態

    結構：
      ① 杯身：U 形回調（10-40% 跌幅），持續 15-120 根 K 線
      ② 杯右緣：價格回到杯左緣相同水平
      ③ 柄：從杯右緣回調 10-30%，縮量
      ④ 突破：價格放量突破柄部高點

    Parameters:
        min_cup_depth: 最小杯深（10%）
        max_cup_depth: 最大杯深（40%）
        handle_retrace: 柄回調比例
    """
    patterns = []
    n = len(df)
    if n < 25:
        return patterns

    # 滑動窗口檢測杯身
    # 杯左緣是局部高點，杯底是局部低點，杯右緣是最新高點
    # 我們從最新 K 線往回找杯的右緣，然後找杯底和左緣

    prices = df['close'].values
    highs = df['high'].values
    volumes = df['volume'].values

    last_idx = n - 1
    lookback_max = min(120, n - 5)  # 最多看 120 根 K 線

    # 找最新 20 根 K 線內的最高點（杯右緣的候選位置）
    right_rim_candidates = []
    for i in range(max(0, last_idx - 20), last_idx + 1):
        # 局部高點：左 3 右 3 都比它低
        if i >= 3 and i < n - 3:
            if (highs[i] >= highs[i-1] and highs[i] >= highs[i-2] and highs[i] >= highs[i-3] and
                highs[i] >= highs[i+1] and highs[i] >= highs[i+2] and highs[i] >= highs[i+3]):
                right_rim_candidates.append(i)

    if not right_rim_candidates:
        # 如果沒有明確局部高點，用最近 10 根 K 線的最高點
        recent_high_idx = last_idx - 5 + list(highs[last_idx - 5:last_idx + 1]).index(max(highs[last_idx - 5:last_idx + 1]))
        right_rim_candidates = [recent_high_idx]

    for right_rim in right_rim_candidates:
        right_price = highs[right_rim]

        # 從右緣向左找杯底（最低點）
        cup_start = max(0, right_rim - lookback_max)
        cup_slice = df.iloc[cup_start:right_rim + 1]
        cup_bottom_idx = cup_slice['low'].idxmin() if hasattr(cup_slice['low'], 'idxmin') else \
                         cup_slice['low'].iloc[cup_slice['low'].values.argmin()]
        # 轉換為位置索引
        bottom_pos = list(df.index).index(cup_bottom_idx) if hasattr(cup_bottom_idx, 'strftime') else \
                     (cup_bottom_idx if isinstance(cup_bottom_idx, int) else
                      list(range(len(df)))[list(df.index).index(cup_bottom_idx)])

        bottom_price = df['low'].iloc[bottom_pos]

        # 杯深檢查
        if right_price <= 0:
            continue
        cup_depth = (right_price - bottom_price) / right_price
        if cup_depth < min_cup_depth or cup_depth > max_cup_depth:
            continue

        # 從杯底向左找杯左緣（左緣高度應該接近右緣）
        left_slice = df.iloc[cup_start:bottom_pos + 1]
        left_rim_idx = left_slice['high'].idxmax() if hasattr(left_slice['high'], 'idxmax') else \
                       left_slice['high'].iloc[left_slice['high'].values.argmax()]
        left_pos = list(df.index).index(left_rim_idx) if hasattr(left_rim_idx, 'strftime') else \
                   (left_rim_idx if isinstance(left_rim_idx, int) else
                    list(range(len(df)))[list(df.index).index(left_rim_idx)])

        left_price = df['high'].iloc[left_pos]

        # 杯左右緣價格接近（誤差 < 15%）
        if right_price <= 0 or left_price <= 0:
            continue
        rim_symmetry = abs(right_price - left_price) / max(right_price, left_price)
        if rim_symmetry > 0.15:
            continue

        # 杯身至少 15 根 K 線（否則太短不算杯）
        cup_length = right_rim - left_pos
        if cup_length < 15:
            continue

        # 杯形檢查：U 形（非 V 形）
        # 杯底前後的價格應該形成平滑曲線
        mid_point = (left_pos + right_rim) // 2
        mid_to_left = abs(mid_point - left_pos)
        mid_to_right = abs(right_rim - mid_point)
        if mid_to_left > 0 and mid_to_right > 0:
            # 檢查杯底是否在中間附近（不是偏左或偏右太多）
            bottom_pos_rel = (bottom_pos - left_pos) / (right_rim - left_pos) if (right_rim - left_pos) > 0 else 0.5
            # U 形杯底應該在中間略偏左（0.3-0.7）
            if bottom_pos_rel < 0.15 or bottom_pos_rel > 0.85:
                continue

        # 確認柄的存在（右緣之後的縮量回調）
        handle_slice = df.iloc[right_rim:min(last_idx + 1, right_rim + 15)]
        if len(handle_slice) < 3:
            handle_patterns = []
        else:
            handle_high = handle_slice['high'].max()
            handle_low = handle_slice['low'].min()
            handle_drop = (right_price - handle_low) / right_price if right_price > 0 else 0

            # 柄回調深度應該在 10-50% 之間
            if handle_drop < 0.05 or handle_drop > 0.50:
                handle_patterns = []
                handle_drop = 0  # 沒有明顯柄，但還是接受（無手柄的 cup）
            else:
                # 柄的成交量應該萎縮
                avg_vol_cup = df['volume'].iloc[left_pos:right_rim + 1].mean()
                avg_vol_handle = handle_slice['volume'].mean()
                vol_shrink = avg_vol_handle / avg_vol_cup if avg_vol_cup > 0 else 1.0

                # 檢查突破：最後一根 K 線是否突破柄部高點
                is_breakout = prices[last_idx] > handle_high * 0.98 if handle_high > 0 else False
                # 最好有成交量確認
                vol_breakout = volumes[last_idx] > avg_vol_handle * 1.3 if avg_vol_handle > 0 else False

                # 權重：柄越淺越好，縮量越好，突破有量越好
                handle_quality = 1.0 - min(handle_drop * 2, 0.5)
                if vol_shrink < 0.8:
                    handle_quality += 0.15  # 縮量加分
                if is_breakout:
                    handle_quality += 0.20  # 突破加分
                if vol_breakout:
                    handle_quality += 0.15  # 放量突破加分

                confidence = min(0.85, 0.55 + handle_quality * 0.3)

                patterns.append(Pattern(
                    name="Cup & Handle",
                    indices=[left_pos, bottom_pos, right_rim],
                    direction="bullish",
                    confidence=round(confidence, 2),
                    metadata={
                        "meaning": f"杯柄形態 — U形杯身({cup_depth:.0%}深, {cup_length}根) + 柄({handle_drop:.0%}回調)",
                        "idx": right_rim,
                        "cup_depth": round(cup_depth, 3),
                        "cup_length": cup_length,
                        "right_rim_price": round(right_price, 2),
                        "left_rim_price": round(left_price, 2),
                        "bottom_price": round(bottom_price, 2),
                        "handle_drop": round(handle_drop, 3),
                        "is_breakout": is_breakout,
                    }
                ))

    return patterns


def detect_pullback_ema_support(df: pd.DataFrame, lookback: int = 5) -> List[Pattern]:
    """
    Pullback to EMA Support（回踩均線支撐）— 趨勢中的健康回調買點

    條件：
      ① 中長期 EMA 處於多頭排列（EMA20 > EMA50 > EMA200）
      ② 價格之前遠高於 EMA20（顯示強勢）
      ③ 最近回調到接近 EMA20 或 EMA50（但不跌破）
      ④ 回調期間成交量縮小（健康回調）

    這是 MU/STX/WDC 在上升趨勢中最常見的形態——「漲上去，回測均線，再漲」
    """
    patterns = []
    n = len(df)
    if n < 60:
        return patterns

    # 檢查均線是否已計算（indicator_calculator 已算好）
    close = df['close'].values
    ema_20 = df.get('ema_20', pd.Series(index=df.index)).values if 'ema_20' in df.columns else None
    ema_50 = df.get('ema_50', pd.Series(index=df.index)).values if 'ema_50' in df.columns else None
    ema_200 = df.get('ema_200', pd.Series(index=df.index)).values if 'ema_200' in df.columns else None
    volume = df['volume'].values if 'volume' in df.columns else None

    if ema_20 is None or ema_50 is None:
        return patterns

    # 確認多頭排列（EMA200 可選：只有 90 天數據時可能為 NaN）
    ema200_valid = ema_200 is not None and not pd.isna(ema_200[-5:].mean()) if len(ema_200) >= 5 else False
    if ema200_valid:
        if not (ema_20[-1] > ema_50[-1] > ema_200[-1]):
            return patterns
    else:
        # 無 EMA200 時，只要求 EMA20 > EMA50（仍然算是多頭排列）
        if not (ema_20[-1] > ema_50[-1]):
            return patterns

    # 2. 價格之前（5-15 天前）明顯高於 EMA20（>3% 溢價——顯示強勢）
    for i in range(min(lookback + 10, n - 1), max(lookback, 0), -1):
        pct_above = (close[i] - ema_20[i]) / ema_20[i] if ema_20[i] > 0 else 0
        if pct_above > 0.03:
            lookback_idx = i
            break
    else:
        return patterns  # 從未顯著高於 EMA20，不是強勢股

    # 3. 當前價格接近 EMA20 或 EMA50（在 1-5% 之內）
    last_close = close[-1]
    pct_from_ema20 = (last_close - ema_20[-1]) / ema_20[-1] if ema_20[-1] > 0 else 0
    pct_from_ema50 = (last_close - ema_50[-1]) / ema_50[-1] if ema_50[-1] > 0 else 0

    # 距離 EMA20 在 0-3% 範圍內（接近但未跌穿）
    at_ema20 = -0.01 <= pct_from_ema20 <= 0.03
    # 距離 EMA50 在 0-2% 範圍內（更深回調但仍在均線之上）
    at_ema50 = -0.01 <= pct_from_ema50 <= 0.02

    if not (at_ema20 or at_ema50):
        return patterns

    # 4. 成交量萎縮（回調應縮量）
    if volume is not None and n >= 25:
        recent_vol_avg = df['volume'].iloc[-5:].mean() if len(df) >= 5 else 0
        prior_vol_avg = df['volume'].iloc[-25:-5].mean() if len(df) >= 25 else 0
        vol_shrink = recent_vol_avg / prior_vol_avg if prior_vol_avg > 0 else 1.0
    else:
        vol_shrink = 1.0

    # 5. 計算信心度
    # 越接近 EMA20，越縮量，信心越高
    dist_factor = 1.0 - abs(pct_from_ema20) / 0.03 if at_ema20 else 0.8
    vol_factor = 1.0 - min(vol_shrink, 1.0) * 0.3  # 縮量加分
    trend_factor = 0.15 if ema_20[-1] > ema_50[-1] else 0  # 多頭排列加分

    confidence = min(0.85, 0.50 + dist_factor * 0.20 + vol_factor * 0.15 + trend_factor)

    # 計算支撐均線名稱和價格
    if at_ema20:
        support_ma = "EMA20"
        support_price = round(ema_20[-1], 2)
    else:
        support_ma = "EMA50"
        support_price = round(ema_50[-1], 2)

    patterns.append(Pattern(
        name="Pullback EMA20",
        indices=[n - 1],
        direction="bullish",
        confidence=round(confidence, 2),
        metadata={
            "meaning": f"回踩{support_ma}支撐 — 多頭排列中健康回調，縮量確認",
            "idx": n - 1,
            "support_ma": support_ma,
            "support_price": support_price,
            "pct_from_ema20": round(pct_from_ema20 * 100, 2),
            "pct_from_ema50": round(pct_from_ema50 * 100, 2),
            "vol_shrink_ratio": round(vol_shrink, 2),
        }
    ))

    return patterns


def detect_consolidation_breakout(df: pd.DataFrame,
                                  lookback: int = 20,
                                  tolerance: float = 0.05) -> List[Pattern]:
    """
    Consolidation Breakout（整理突破）— 橫向盤整後的放量突破

    條件：
      ① 過去 N 根 K 線價格在一個窄幅區間內波動（橫盤整理）
      ② 區間寬度 < 15%（相對於區間中位數）
      ③ 最新 K 線收盤價突破區間上限
      ④ 最好有成交量放大確認

    適合捕捉：強勢股在突破前橫向洗盤後的爆發點
    """
    patterns = []
    n = len(df)
    if n < lookback + 3:
        return patterns

    close = df['close'].values
    high = df['high'].values
    volume = df['volume'].values

    # 分析最近 N 根 K 線的價格區間
    recent_high = max(high[-lookback:])
    recent_low = min(df['low'].values[-lookback:])
    recent_mid = (recent_high + recent_low) / 2

    if recent_mid <= 0:
        return patterns

    # 區間寬度
    range_pct = (recent_high - recent_low) / recent_mid

    # 橫盤條件：區間 < 20%（相對於中位數）
    if range_pct > 0.20:
        return patterns

    # 確認最近 5 根沒有大幅偏離區間（保持在區間內整理）
    recent_5 = close[-5:]
    for c in recent_5:
        if c < recent_low * (1 - tolerance) or c > recent_high * (1 + tolerance):
            return patterns  # 過早突破或假突破

    # 最新收盤價突破區間上限
    last_close = close[-1]
    is_breakout = last_close > recent_high * 0.995

    if not is_breakout:
        return patterns

    # 成交量確認
    avg_vol_lookback = df['volume'].iloc[-lookback:].mean() if lookback <= n else volume[:n].mean()
    last_vol = volume[-1]
    vol_ratio = last_vol / avg_vol_lookback if avg_vol_lookback > 0 else 1.0
    vol_confirmed = vol_ratio > 1.3

    # 信心度
    # 區間越窄（越低波動）、突破有量、突破幅度越大 → 越高信心
    tightness_factor = 1.0 - min(range_pct / 0.15, 1.0) * 0.3
    vol_factor = 0.3 if vol_confirmed else 0.1
    confidence = min(0.85, 0.45 + tightness_factor * 0.25 + vol_factor)

    patterns.append(Pattern(
        name="Consolidation Breakout",
        indices=[n - 1],
        direction="bullish",
        confidence=round(confidence, 2),
        metadata={
            "meaning": f"整理突破 — {lookback}根K線橫盤({range_pct:.1%}區間)後放量突破",
            "idx": n - 1,
            "range_pct": round(range_pct, 3),
            "breakout_vol_ratio": round(vol_ratio, 2),
            "vol_confirmed": vol_confirmed,
            "consolidation_high": round(recent_high, 2),
            "consolidation_low": round(recent_low, 2),
        }
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
            lambda i, _df=df, _d0=detect_inverted_hammer: _d0(_df, i),
            lambda i, _df=df, _d0=detect_morning_star:  _d0(_df, i),
            lambda i, _df=df, _d0=detect_evening_star:  _d0(_df, i),
            lambda i, _df=df, _d0=detect_engulfing:    _d0(_df, i),
            lambda i, _df=df, _d0=detect_harami:        _d0(_df, i),
            lambda i, _df=df, _d0=detect_piercing_line:  _d0(_df, i),
            lambda i, _df=df, _d0=detect_dark_cloud_cover: _d0(_df, i),
            # 鉗子底/頂已移除（Edward 要求）
            lambda i, _df=df, _d0=detect_three_white_soldiers: _d0(_df, i),
            lambda i, _df=df, _d0=detect_three_black_crows: _d0(_df, i),
            lambda i, _df=df, _d0=detect_three_inside_up:   _d0(_df, i),
            lambda i, _df=df, _d0=detect_three_outside_up:  _d0(_df, i),
            lambda i, _df=df, _d0=detect_rising_three_methods: _d0(_df, i),
            lambda i, _df=df, _d0=detect_falling_three_methods: _d0(_df, i),
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
        detect_cup_handle,           # ✨ 新增：杯柄
        detect_pullback_ema_support, # ✨ 新增：回踩均線
        detect_consolidation_breakout,  # ✨ 新增：整理突破
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
