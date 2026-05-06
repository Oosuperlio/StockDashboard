"""
pattern_annotator.py — 將形態標記疊加到 Plotly 圖表
=======================================================
支援：
  - 燭線形態：菱形標記 + 文字標籤
  - 支撐/壓力位：水平線
  - 頭肩頂/底、雙頂/底：頸線 + 區域陰影
  - 旗形、三角形：連線
"""

from __future__ import annotations
import plotly.graph_objects as go
from pattern_detector import Pattern


# 形態顏色映射
PATTERN_COLORS = {
    "Doji":                  "#FFD700",   # 金色
    "Hammer":                "#00FF7F",   # 深綠
    "Shooting Star":         "#FF4444",   # 紅
    "Bullish Engulfing":     "#00FF7F",   # 深綠
    "Bearish Engulfing":     "#FF4444",   # 紅
    "Morning Star":          "#00FF7F",   # 深綠
    "Evening Star":          "#FF4444",   # 紅
    "Bullish Harami":        "#00CED1",   # 暗綠
    "Bearish Harami":        "#FF6B6B",   # 暗紅
    "Head & Shoulders":       "#FF8C00",   # 橙
    "Inverse H&S":            "#32CD32",   # 亮綠
    "Double Top":             "#FF6347",   # 橙紅
    "Double Bottom":          "#3CB371",   # 中綠
    "Ascending Triangle":     "#9370DB",   # 中紫
    "Descending Triangle":    "#FF69B4",   # 粉紅
    "Symmetrical Triangle":   "#808080",   # 灰
    "Bull Flag":              "#20B2AA",   # 藍綠
    "Bear Flag":              "#F4A460",   # 沙色
    "Support":                "#4169E1",   # 皇家藍
    "Resistance":             "#DC143C",   # 深紅
    "MA Bullish Alignment":   "#00FA9A",   # 翠綠
    "MA Bearish Alignment":   "#FF4500",   # 橙紅
    "Volume + Price Breakout": "#7B68EE",  # 紫藍
    "Volume + Price Breakdown": "#FF1493", # 深粉
}

# 形態方向前綴
PREFIX = {
    "bullish": "▲",
    "bearish": "▼",
    "neutral": "◆",
}

# 形態中文名稱
PATTERN_NAMES_CN = {
    "Doji":                    "十字星",
    "Hammer":                  "錘子",
    "Shooting Star":           "流星",
    "Bullish Engulfing":       "看漲吞噬",
    "Bearish Engulfing":       "看跌吞噬",
    "Morning Star":            "早晨之星",
    "Evening Star":            "黃昏之星",
    "Bullish Harami":          "看漲內含線",
    "Bearish Harami":          "看跌內含線",
    "Head & Shoulders":        "頭肩頂",
    "Inverse H&S":             "倒頭肩底",
    "Double Top":              "雙頂",
    "Double Bottom":           "雙底",
    "Ascending Triangle":      "上升三角形",
    "Descending Triangle":     "下降三角形",
    "Symmetrical Triangle":    "對稱三角形",
    "Bull Flag":               "牛市旗形",
    "Bear Flag":               "熊市旗形",
    "Support":                 "支撐位",
    "Resistance":              "壓力位",
    "MA Bullish Alignment":    "均線多頭排列",
    "MA Bearish Alignment":    "均線空頭排列",
    "Volume + Price Breakout": "放量突破",
    "Volume + Price Breakdown": "放量破底",
}

def get_color(pattern: Pattern) -> str:
    return PATTERN_COLORS.get(pattern.name, "#888888")


def add_pattern_markers(fig: go.Figure, df, patterns: list, dates: list) -> go.Figure:
    """
    將形態標記疊加到現有的 candlestick figure 上
    """
    # Convert all numeric columns to float to avoid Decimal vs float errors
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df = df.copy()
            df[col] = df[col].astype(float)
    n = len(df)
    idx_to_x = lambda i: i  # 整數坐標系

    # ── 1. 收集形勢標記（散點）──
    import sys
    for p in patterns:
        color = get_color(p)
        prefix = PREFIX.get(p.direction, "◆")
        mid_idx = p.metadata.get("idx", p.indices[len(p.indices) // 2])

        if p.name in ("Support", "Resistance"):
            # 支撐/壓力位用水平線，稍後統一處理
            continue

        # 主標記點：取形態區間的中間 K 線
        candle_mid = fig.data[0].x[mid_idx] if mid_idx < len(fig.data[0].x) else mid_idx
        high_val = df["high"].iloc[mid_idx] if mid_idx < len(df) else df["high"].iloc[-1]

        # 標籤文字（中文）
        label_text = f"{prefix} {PATTERN_NAMES_CN.get(p.name, p.name)}"

        # DEBUG: 記錄每個標記放到哪個座標
        date_at_idx = dates[mid_idx] if mid_idx < len(dates) else dates[-1]
        sys.stderr.write(f"[ANNOTATOR DEBUG] pattern={p.name} mid_idx={mid_idx} x={candle_mid} date={date_at_idx} label={label_text}\n")

        # 添加標記（倒三角 ▼ 置於 K 線上方）
        fig.add_trace(go.Scatter(
            x=[candle_mid],
            y=[high_val * 1.005],  # 稍微高於 K 線頂部
            mode="markers+text",
            marker=dict(
                symbol="triangle-down",
                size=14,
                color=color,
                line=dict(width=1, color="white"),
            ),
            text=[label_text],
            textposition="top center",
            textfont=dict(size=9, color=color),
            hoverinfo="text",
            hovertext=f"{PATTERN_NAMES_CN.get(p.name, p.name)}<br>{p.metadata.get('meaning', '')}<br>信心: {p.confidence:.0%}",
            showlegend=False,
        ))

    # ── 2. 支撐位 / 壓力位水平線 ──
    for p in patterns:
        if p.name not in ("Support", "Resistance"):
            continue
        level = p.metadata.get("level")
        if level is None:
            continue
        line_color = "#4169E1" if p.name == "Support" else "#DC143C"
        prefix = "───" if p.name == "Support" else "───"

        # 取該水平最後出現的 index 作為 x 範圍起點
        last_idx = max(p.indices)
        x_start = last_idx

        fig.add_shape(
            type="line",
            x0=x_start, x1=n - 1,
            y0=level, y1=level,
            line=dict(color=line_color, width=1.5, dash="dash"),
            layer="above",
        )
        fig.add_trace(go.Scatter(
            x=[x_start],
            y=[level],
            mode="text",
            text=[f"{PATTERN_NAMES_CN.get(p.name, p.name)}: {level:.2f}"],
            textposition="top right",
            textfont=dict(size=9, color=line_color),
            hoverinfo="text",
            hovertext=f"{PATTERN_NAMES_CN.get(p.name, p.name)} @ {level}<br>{p.metadata.get('meaning', '')}",
            showlegend=False,
        ))

    # ── 3. 頭肩頂/底、雙頂/底 — 頸線 + 陰影區域 ──
    for p in patterns:
        if p.name not in ("Head & Shoulders", "Inverse H&S", "Double Top", "Double Bottom"):
            continue
        neckline = p.metadata.get("neckline")
        if neckline is None:
            continue

        first_idx = p.indices[0]
        last_idx = p.indices[-1]

        if p.name in ("Head & Shoulders", "Double Top"):
            # 頸線上方陰影
            y0 = neckline
            y1 = df["high"].iloc[first_idx:last_idx + 1].max()
        else:
            y0 = df["low"].iloc[first_idx:last_idx + 1].min()
            y1 = neckline

        fig.add_shape(
            type="rect",
            x0=first_idx, x1=last_idx,
            y0=y0, y1=y1,
            fillcolor="rgba(255,100,100,0.08)" if "Top" in p.name or "Shoulders" in p.name else "rgba(100,255,100,0.08)",
            line=dict(color="rgba(255,255,255,0.1)", width=1),
            layer="below",
        )
        # 頸線
        fig.add_shape(
            type="line",
            x0=first_idx, x1=last_idx,
            y0=neckline, y1=neckline,
            line=dict(color=get_color(p), width=1.5, dash="dot"),
            layer="above",
        )

    # ── 4. 三角形 / 旗形 — 連線 ──
    for p in patterns:
        if p.name not in ("Ascending Triangle", "Descending Triangle",
                          "Symmetrical Triangle", "Bull Flag", "Bear Flag"):
            continue
        idx0, idx1 = p.indices[0], p.indices[-1]

        if "Triangle" in p.name:
            # 畫收敛线
            # 上轨：高点连线
            fig.add_trace(go.Scatter(
                x=[idx0, idx1],
                y=[df["high"].iloc[idx0:idx0 + 5].max(), df["high"].iloc[idx1 - 5:idx1 + 1].max()],
                mode="lines",
                line=dict(color=get_color(p), width=1.5, dash="dash"),
                hoverinfo="skip",
                showlegend=False,
            ))
            # 下轨：低点连线
            fig.add_trace(go.Scatter(
                x=[idx0, idx1],
                y=[df["low"].iloc[idx0:idx0 + 5].min(), df["low"].iloc[idx1 - 5:idx1 + 1].min()],
                mode="lines",
                line=dict(color=get_color(p), width=1.5, dash="dash"),
                hoverinfo="skip",
                showlegend=False,
            ))
        elif "Flag" in p.name:
            # 畫旗桿 + 旗面
            fig.add_trace(go.Scatter(
                x=[idx0, idx1],
                y=[df["close"].iloc[idx0], df["close"].iloc[idx1]],
                mode="lines",
                line=dict(color=get_color(p), width=1.5),
                hoverinfo="skip",
                showlegend=False,
            ))

    return fig


def build_pattern_legend(patterns: list) -> str:
    """生成形態圖例說明（用於 hover 或側邊欄）"""
    if not patterns:
        return "暫未識別到明顯形態"
    lines = []
    for p in patterns[:15]:  # 最多顯示15個
        prefix = "🟢" if p.direction == "bullish" else ("🔴" if p.direction == "bearish" else "🟡")
        lines.append(f"{prefix} **{PATTERN_NAMES_CN.get(p.name, p.name)}** — {p.metadata.get('meaning', '')}")
    return "\n".join(lines)
