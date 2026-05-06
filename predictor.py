"""
predictor.py — 股價走勢預判引擎
=================================
根據識別到的形態組合，生成大膽的短期（3-7天）/中期（2-4周）預判
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional
import pandas as pd
from pattern_detector import Pattern, detect_all_patterns, get_latest_patterns


# ─────────────────────────────────────────────
# 形態方向權重
# ─────────────────────────────────────────────

BULLISH_PATTERNS = {
    "Hammer", "Bullish Engulfing", "Morning Star", "Bullish Harami",
    "Inverse H&S", "Double Bottom", "Ascending Triangle",
    "Bull Flag", "Support", "MA Bullish Alignment", "Volume + Price Breakout",
}

BEARISH_PATTERNS = {
    "Shooting Star", "Bearish Engulfing", "Evening Star", "Bearish Harami",
    "Head & Shoulders", "Double Top", "Descending Triangle",
    "Bear Flag", "Resistance", "MA Bearish Alignment", "Volume + Price Breakdown",
}

NEUTRAL_PATTERNS = {
    "Doji", "Symmetrical Triangle",
}

# ─────────────────────────────────────────────
# 輸出結構
# ─────────────────────────────────────────────

@dataclass
class Prediction:
    direction: str          # 'bullish' | 'bearish' | 'neutral'
    horizon: str           # 'short' (3-7天) | 'medium' (2-4周)
    confidence: float      # 0.0 ~ 1.0
    target_price: Optional[float]
    stop_loss: Optional[float]
    summary: str           # 一句話總結
    details: List[str]     # 詳細理由列表
    warnings: List[str]     # 風險提示


# ─────────────────────────────────────────────
# 核心預判邏輯
# ─────────────────────────────────────────────

def score_patterns(patterns: List[Pattern]) -> tuple[float, float, float]:
    """
    根據形態計算多空分數
    返回 (bull_score, bear_score, neutral_score)，範圍大約 -10 ~ +10
    """
    bull = 0.0
    bear = 0.0
    for p in patterns:
        w = p.confidence * 2.0  # 權重：置信度 × 形態強度
        if p.name in BULLISH_PATTERNS:
            bull += w
        elif p.name in BEARISH_PATTERNS:
            bear += w
        else:
            bull += w * 0.3  # 中性形態輕微加分
    return bull, bear, bull - bear


def detect_momentum(df: pd.DataFrame) -> str:
    """根據最近 5 根 K 線的方向判断短期動量"""
    if len(df) < 5:
        return "neutral"
    recent = df.tail(5)
    up = (recent["close"].diff() > 0).sum()
    if up >= 4:
        return "strong_bullish"
    elif up <= 1:
        return "strong_bearish"
    elif up == 3:
        return "bullish"
    elif up == 2:
        return "neutral"
    return "bearish"


def estimate_ATR(df: pd.DataFrame, n=14) -> float:
    """估算 ATR（Average True Range）用於止損計算"""
    if len(df) < n:
        return df["high"].sub(df["low"]).mean()
    tr = df["high"].tail(n).sub(df["low"].tail(n))
    return tr.mean()


def predict(df: pd.DataFrame, lookback: int = 30) -> Prediction:
    """
    主要預判函數
    """
    if df is None or len(df) < 10:
        return Prediction(
            direction="neutral", horizon="short", confidence=0.0,
            target_price=None, stop_loss=None,
            summary="數據不足，無法預判",
            details=[], warnings=["K 線數據少於 10 根"]
        )

    patterns = get_latest_patterns(df, lookback=lookback)
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df = df.copy()
            df[col] = df[col].astype(float)
    last_close = df["close"].iloc[-1]
    atr = estimate_ATR(df)

    bull_score, bear_score, net_score = score_patterns(patterns)
    momentum = detect_momentum(df)

    # ── 方向判斷 ──
    if net_score >= 3.0:
        direction = "bullish"
    elif net_score <= -3.0:
        direction = "bearish"
    elif net_score >= 1.5 and momentum.startswith("strong_bullish"):
        direction = "bullish"
    elif net_score <= -1.5 and momentum.startswith("strong_bearish"):
        direction = "bearish"
    else:
        direction = "neutral"

    # ── 信心值 ──
    confidence = min(abs(net_score) / 10 + (len(patterns) * 0.05), 0.95)

    # ── 目標價 & 止損 ──
    if direction == "bullish":
        # 目標：基於 ATR × 動量
        move_mult = 2.0 if momentum == "strong_bullish" else 1.5
        target_price = round(last_close + atr * move_mult, 2)
        stop_loss = round(last_close - atr * 1.5, 2)
    elif direction == "bearish":
        move_mult = 2.0 if momentum == "strong_bearish" else 1.5
        target_price = round(last_close - atr * move_mult, 2)
        stop_loss = round(last_close + atr * 1.5, 2)
    else:
        target_price = None
        stop_loss = None

    # ── 視角 ──
    horizon = "short" if len(df) < 60 else "medium"

    # ── 理由構建 ──
    details = []
    warnings = []

    # 加入形態理由
    bullish_found = [p.name for p in patterns if p.name in BULLISH_PATTERNS]
    bearish_found = [p.name for p in patterns if p.name in BEARISH_PATTERNS]

    if bullish_found:
        details.append(f"✅ 看漲形態：{', '.join(set(bullish_found))}")
    if bearish_found:
        details.append(f"🔴 看跌形態：{', '.join(set(bearish_found))}")

    # 動量理由
    if momentum == "strong_bullish":
        details.append(f"📈 強勁動量：近5根K線4根以上收高")
    elif momentum == "strong_bearish":
        details.append(f"📉 強勁動量：近5根K線4根以上收低")

    # 趨勢理由
    if direction == "bullish":
        if net_score > 5:
            details.append(f"🚀 多頭信號極強（Score: +{net_score:.1f}），多形態共振")
        elif net_score > 3:
            details.append(f"📈 多頭信號明確（Score: +{net_score:.1f}）")
    elif direction == "bearish":
        if net_score < -5:
            details.append(f"🔻 空頭信號極強（Score: {net_score:.1f}），多形態共振")
        elif net_score < -3:
            details.append(f"📉 空頭信號明確（Score: {net_score:.1f}）")
    else:
        details.append(f"⚖️ 多空僵持（Score: {net_score:+.1f}），建議觀望")

    # ── 風險提示 ──
    if len(patterns) >= 5:
        warnings.append("⚠️ 形態過多，短期信號混雜，需謹慎")
    if abs(net_score) < 2:
        warnings.append("⚠️ 信心不足，建議等待確認")
    if direction == "bullish" and momentum in ("strong_bearish", "bearish"):
        warnings.append("⚠️ 形態看漲但短期動量偏空，注意逆勢風險")
    if direction == "bearish" and momentum in ("strong_bullish", "bullish"):
        warnings.append("⚠️ 形態看跌但短期動量偏多，注意逆勢風險")

    # ── 總結句 ──
    if direction == "bullish":
        if confidence >= 0.75:
            summary = f"🟢 強烈看漲！目標價 ${target_price}（+{(target_price/last_close-1)*100:.1f}%），現價 ${last_close}"
        else:
            summary = f"🟢 謹慎看漲，目標價 ${target_price}（+{(target_price/last_close-1)*100:.1f}%），現價 ${last_close}"
    elif direction == "bearish":
        if confidence >= 0.75:
            summary = f"🔴 強烈看跌！目標價 ${target_price}（{(target_price/last_close-1)*100:.1f}%），現價 ${last_close}"
        else:
            summary = f"🔴 謹慎看跌，目標價 ${target_price}（{(target_price/last_close-1)*100:.1f}%），現價 ${last_close}"
    else:
        summary = f"⚪ 中性，等待方向確認"

    return Prediction(
        direction=direction,
        horizon=horizon,
        confidence=round(confidence, 2),
        target_price=target_price,
        stop_loss=stop_loss,
        summary=summary,
        details=details,
        warnings=warnings,
    )


def format_prediction_html(pred: Prediction) -> str:
    """將 Prediction 格式化為 HTML 字符串"""
    if pred.confidence == 0:
        return f"<div class='prediction-box neutral'><b>{pred.summary}</b></div>"

    color = {"bullish": "#00FF7F", "bearish": "#FF4444", "neutral": "#FFD700"}[pred.direction]
    box_class = {"bullish": "bullish", "bearish": "bearish", "neutral": "neutral"}[pred.direction]

    target_str = f"${pred.target_price:.2f}" if pred.target_price else "—"
    stop_str = f"${pred.stop_loss:.2f}" if pred.stop_loss else "—"
    conf_pct = f"{pred.confidence * 100:.0f}%"

    details_html = "".join(f"<li>{d}</li>" for d in pred.details)
    warnings_html = "".join(f"<li>{w}</li>" for w in pred.warnings) if pred.warnings else ""

    return f"""
    <div class="prediction-box {box_class}">
        <div class="pred-header">{pred.summary}</div>
        <div class="pred-meta">
            <span>信心: <b>{conf_pct}</b></span>
            <span>目標: <b>{target_str}</b></span>
            <span>止損: <b>{stop_str}</b></span>
        </div>
        <ul class="pred-details">{details_html}</ul>
        {f'<ul class="pred-warnings">{warnings_html}</ul>' if warnings_html else ''}
    </div>
    """
