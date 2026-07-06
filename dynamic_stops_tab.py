"""
dynamic_stops_tab.py — Dynamic Stop-Loss & Take-Profit Monitoring Dashboard Tab

Displays:
  1. Overview cards: total at-risk value, alerts count, safe positions
  2. Per-stock monitoring table with stop/TP levels, distance %, status
  3. Color-coded alert system: safe / approaching / breached
  4. Signal recommendation for action
"""

from __future__ import annotations

import json
import os
from datetime import datetime

import streamlit as st
import yfinance as yf
import pandas as pd

# ─── Paths ────────────────────────────────────────────────────────────────────

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "data", "dynamic_stops", "stop_config.json")


def _load_config() -> dict:
    """Load the consolidated stop/TP config."""
    if not os.path.exists(CONFIG_PATH):
        st.error(f"⚠️ 找不到 stop_config.json — 請先執行回測腳本")
        return {"tickers": {}}
    with open(CONFIG_PATH) as f:
        return json.load(f)


def _fetch_live_prices(tickers: list[str]) -> dict[str, float]:
    """Fetch current prices for all tickers via yfinance (fast, 1d)."""
    try:
        data = yf.download(tickers, period="1d", auto_adjust=True, progress=False)
        close = data["Close"] if isinstance(data.columns, pd.MultiIndex) else data
        prices = {}
        for tk in tickers:
            try:
                v = close[tk].iloc[-1] if isinstance(close, pd.DataFrame) else close.iloc[-1]
                prices[tk] = round(float(v), 2)
            except Exception:
                prices[tk] = None
        return prices
    except Exception as e:
        st.warning(f"⚠️ 即時價格獲取失敗: {e}")
        return {}


# ─── Status Helpers ───────────────────────────────────────────────────────────

def _compute_status(price: float, stop_price: float, tp1_price: float | None) -> str:
    """Determine the alert status of a position."""
    if price is None:
        return "unknown"
    if price <= stop_price:
        return "breached_stop"
    if tp1_price and price >= tp1_price:
        return "approaching_tp"
    # Check distance to stop
    dist_stop = (price - stop_price) / price * 100
    if dist_stop <= 2.0:
        return "near_stop"
    if tp1_price:
        dist_tp = (tp1_price - price) / price * 100
        if dist_tp <= 3.0:
            return "near_tp"
    return "safe"


_STATUS_EMOJI = {
    "breached_stop":   "🔴",
    "near_stop":       "🟡",
    "near_tp":         "🟢",
    "approaching_tp":  "🟢",
    "safe":            "⚪",
    "unknown":         "❓",
}

_STATUS_LABEL = {
    "breached_stop":   "⚠️ 觸及止損！",
    "near_stop":       "接近止損",
    "near_tp":         "接近止盈",
    "approaching_tp":  "觸及止盈",
    "safe":            "安全",
    "unknown":         "數據不足",
}


def _compute_ema_status(ticker: str, price: float, trend: str) -> tuple[str, str]:
    """Return (emoji, label) for EMA trend context."""
    if trend == "strong_uptrend":
        return "🚀", "強勢上升"
    if trend == "uptrend":
        return "📈", "緩步上升"
    if trend == "pullback_in_uptrend":
        return "🔄", "回調中"
    if trend == "downtrend":
        return "⬇️", "下跌趨勢"
    if trend == "weak":
        return "⚠️", "弱勢"
    return "➖", "—"


# ─── Render ───────────────────────────────────────────────────────────────────

def render_dynamic_stops_tab():
    config = _load_config()
    tickers_data = config.get("tickers", {})
    tickers = list(tickers_data.keys())

    if not tickers:
        st.info("暫無止損配置數據。請先執行回測並生成 stop_config.json")
        return

    st.markdown("## 🛡️ 動態止損監控")
    st.caption(f"最後更新: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | 數據來源: 3年回測 + EMA/ATR/支撐阻力分析")

    # ── Fetch live prices ──
    live_prices = _fetch_live_prices(tickers)

    # ── Compute status per ticker ──
    rows = []
    alerts = {"breached": 0, "near_stop": 0, "near_tp": 0}
    total_value_risk = 0.0

    for tk in tickers:
        cfg = tickers_data[tk]
        price = live_prices.get(tk)
        if price is None:
            price = cfg.get("current_price", 0)

        sl = cfg["stop_loss"]
        stop_price = sl["price"]
        tp_tiers = cfg["take_profit"]["tiers"]
        tp1_price = tp_tiers[0]["price"] if tp_tiers and tp_tiers[0]["price"] else None

        # Status
        status = _compute_status(price, stop_price, tp1_price)
        if status == "breached_stop":
            alerts["breached"] += 1
        elif status == "near_stop":
            alerts["near_stop"] += 1
        elif status in ("near_tp", "approaching_tp"):
            alerts["near_tp"] += 1

        # Distances
        dist_to_stop = (price - stop_price) / price * 100 if price > 0 else 0
        dist_to_tp1 = (tp1_price - price) / price * 100 if tp1_price and price > 0 else None

        # EMA trend
        trend_emoji, trend_label = _compute_ema_status(tk, price, cfg["trend"])

        # Trailing stop active?
        trailing = cfg.get("trailing_stop", {})
        trailing_active = trailing.get("current_level") is not None

        rows.append({
            "ticker": tk,
            "name": cfg.get("name", "").split("(")[0].strip(),
            "price": price,
            "trend_emoji": trend_emoji,
            "trend_label": trend_label,
            "stop_price": stop_price,
            "stop_pct": (stop_price - price) / price * 100 if price > 0 else sl["pct_from_price"],
            "stop_method": sl["method"],
            "dist_to_stop": dist_to_stop,
            "trailing_active": trailing_active,
            "trailing_level": trailing.get("current_level"),
            "trailing_method": trailing.get("method", "—"),
            "tp1_price": tp1_price,
            "tp1_pct": tp_tiers[0]["pct_from_price"] if tp_tiers else None,
            "dist_to_tp1": dist_to_tp1,
            "tp2_price": tp_tiers[1]["price"] if len(tp_tiers) > 1 else None,
            "tp2_pct": tp_tiers[1]["pct_from_price"] if len(tp_tiers) > 1 else None,
            "atr_pct": cfg["atr"]["pct"],
            "atr_mult": cfg["recommended_atr_multiplier"],
            "best_ema": cfg["best_ema_scheme"],
            "status": status,
            "status_emoji": _STATUS_EMOJI.get(status, "❓"),
            "status_label": _STATUS_LABEL.get(status, "—"),
        })

        # Accumulate at-risk value
        total_value_risk += price * (dist_to_stop / 100) if dist_to_stop < 0 else 0

    df = pd.DataFrame(rows)

    # ── Overview cards ──────────────────────────────────────────────────────
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        safe_count = sum(1 for r in rows if r["status"] == "safe")
        st.markdown(
            f'<div class="card"><div class="card-header">🛡️ 安全持倉</div>'
            f'<div class="card-value" style="color:#48bb78;">{safe_count}/{len(rows)}</div></div>',
            unsafe_allow_html=True,
        )

    with col2:
        sl_color = "#fc8181" if alerts["breached"] > 0 else "#48bb78"
        st.markdown(
            f'<div class="card"><div class="card-header">🔴 觸及止損</div>'
            f'<div class="card-value" style="color:{sl_color};">{alerts["breached"]}</div></div>',
            unsafe_allow_html=True,
        )

    with col3:
        ns_color = "#ecc94b" if alerts["near_stop"] > 0 else "#718096"
        st.markdown(
            f'<div class="card"><div class="card-header">🟡 接近止損</div>'
            f'<div class="card-value" style="color:{ns_color};">{alerts["near_stop"]}</div></div>',
            unsafe_allow_html=True,
        )

    with col4:
        st.markdown(
            f'<div class="card"><div class="card-header">🟢 接近止盈</div>'
            f'<div class="card-value" style="color:#48bb78;">{alerts["near_tp"]}</div></div>',
            unsafe_allow_html=True,
        )

    st.markdown("---")

    # ── Detailed monitoring table ───────────────────────────────────────────
    st.markdown("### 📋 持倉止損監控明細")

    # Sort: breached first, then near_stop, then near_tp, then safe
    status_order = {"breached_stop": 0, "near_stop": 1, "near_tp": 2, "approaching_tp": 3, "safe": 4, "unknown": 5}
    df["_sort"] = df["status"].map(lambda s: status_order.get(s, 5))
    df = df.sort_values("_sort").drop(columns=["_sort"])

    # Build HTML table
    table_html = """
    <style>
    .stop-table { width: 100%; border-collapse: collapse; font-size: 13px; }
    .stop-table th { background: #1e2533; color: #718096; font-weight: 600;
                     padding: 10px 8px; text-align: left; border-bottom: 2px solid #2d3748;
                     font-size: 11px; letter-spacing: 0.05em; text-transform: uppercase; }
    .stop-table td { padding: 10px 8px; border-bottom: 1px solid #1a2035;
                     color: #cbd5e0; vertical-align: middle; }
    .stop-table tr:hover td { background: rgba(255,255,255,0.02); }
    .stop-breach { background: rgba(252,129,129,0.08) !important; }
    .stop-near { background: rgba(236,201,75,0.08) !important; }
    .stop-tp-near { background: rgba(72,187,120,0.08) !important; }
    .tag { display: inline-block; padding: 2px 8px; border-radius: 4px;
            font-size: 11px; font-weight: 600; }
    .tag-sl { background: rgba(252,129,129,0.15); color: #fc8181; }
    .tag-tp { background: rgba(72,187,120,0.15); color: #48bb78; }
    .tag-trail { background: rgba(107,203,255,0.15); color: #6bcbff; }
    .tag-safe { background: rgba(72,187,120,0.1); color: #48bb78; }
    .tag-warn { background: rgba(236,201,75,0.15); color: #ecc94b; }
    .tag-danger { background: rgba(252,129,129,0.2); color: #fc8181; }
    </style>
    <table class="stop-table">
    <thead>
    <tr>
        <th>股票</th>
        <th>趨勢</th>
        <th>現價</th>
        <th>🛑 止損</th>
        <th>距止損</th>
        <th>📉 追蹤</th>
        <th>🎯 TP1</th>
        <th>距TP1</th>
        <th>🎯 TP2</th>
        <th>ATR</th>
        <th>狀態</th>
    </tr>
    </thead>
    <tbody>
    """

    for _, r in df.iterrows():
        row_class = ""
        if r["status"] == "breached_stop":
            row_class = ' class="stop-breach"'
        elif r["status"] == "near_stop":
            row_class = ' class="stop-near"'
        elif r["status"] in ("near_tp", "approaching_tp"):
            row_class = ' class="stop-tp-near"'

        # Dist to stop color
        dist_stop_color = "#fc8181" if r["dist_to_stop"] < 3 else "#ecc94b" if r["dist_to_stop"] < 5 else "#48bb78"

        # Trailing info
        trailing_html = ""
        if r["trailing_active"]:
            trailing_html = f'<span class="tag tag-trail">EMA ${r["trailing_level"]:<.2f}</span>' if r["trailing_level"] else f'<span class="tag tag-trail">{r["trailing_method"][:12]}…</span>'
        else:
            trailing_html = f'<span style="color:#4a5568;">—</span>'

        # Status badge
        if r["status"] == "breached_stop":
            badge = '<span class="tag tag-danger">⚠️ 觸及止損</span>'
        elif r["status"] == "near_stop":
            badge = f'<span class="tag tag-warn">距離 {r["dist_to_stop"]:.1f}%</span>'
        elif r["status"] in ("near_tp", "approaching_tp"):
            badge = '<span class="tag tag-tp">接近止盈</span>'
        else:
            badge = '<span class="tag tag-safe">安全</span>'

        tp1_price_str = f"${r['tp1_price']:.2f}" if r["tp1_price"] else "—"
        tp1_pct_str = f" ({r['tp1_pct']:+.1f}%)" if r["tp1_pct"] else ""
        tp2_price_str = f"${r['tp2_price']:.2f}" if r["tp2_price"] else "—"
        tp2_pct_str = f" ({r['tp2_pct']:+.1f}%)" if r["tp2_pct"] else ""

        dist_tp1_str = f"{r['dist_to_tp1']:.1f}%" if r["dist_to_tp1"] is not None else "—"

        table_html += f"""
        <tr{row_class}>
            <td><b>{r['ticker']}</b><br><span style="font-size:11px;color:#718096;">{r['name'][:18]}</span></td>
            <td>{r['trend_emoji']} {r['trend_label']}</td>
            <td style="font-weight:600;color:#e2e8f0;">${r['price']:.2f}</td>
            <td><span class="tag tag-sl">${r['stop_price']:.2f} ({r['stop_pct']:+.1f}%)</span></td>
            <td style="color:{dist_stop_color};font-weight:600;">{r['dist_to_stop']:.1f}%</td>
            <td>{trailing_html}</td>
            <td><span class="tag tag-tp">${tp1_price_str}{tp1_pct_str}</span></td>
            <td>{dist_tp1_str}</td>
            <td><span class="tag tag-tp">{tp2_price_str}{tp2_pct_str}</span></td>
            <td style="font-size:11px;color:#718096;">{r['atr_pct']:.1f}% ×{r['atr_mult']:.1f}</td>
            <td>{badge}</td>
        </tr>"""

    table_html += "</tbody></table>"
    st.markdown(table_html, unsafe_allow_html=True)

    st.markdown("---")

    # ── Take-profit progress bars ───────────────────────────────────────────
    st.markdown("### 📊 距目標價進度條")

    cols = st.columns(3)
    for i, r in enumerate(df.to_dict("records")):
        with cols[i % 3]:
            if r["tp1_price"] and r["price"] > 0:
                # Calculate how far along to TP1
                stop = r["stop_price"]
                tp1 = r["tp1_price"]
                if tp1 > stop:
                    progress = (r["price"] - stop) / (tp1 - stop) * 100
                else:
                    progress = 50.0
                progress = max(0, min(100, progress))
                bar_color = "#fc8181" if progress < 33 else "#ecc94b" if progress < 66 else "#48bb78"

                html = f"""
                <div class="card">
                    <div class="card-header">{r['ticker']} → ${tp1:.2f}</div>
                    <div style="display:flex;align-items:center;gap:8px;margin-top:6px;">
                        <span style="font-size:11px;color:#fc8181;">${r['stop_price']:.1f}</span>
                        <div style="flex:1;height:8px;background:#2d3748;border-radius:4px;overflow:hidden;">
                            <div style="height:100%;width:{progress:.0f}%;background:{bar_color};border-radius:4px;transition:width 0.3s;"></div>
                        </div>
                        <span style="font-size:11px;color:#48bb78;">${tp1:.1f}</span>
                    </div>
                    <div style="font-size:12px;color:#718096;margin-top:4px;">
                        <b style="color:{bar_color};">{progress:.0f}%</b> 完成
                        {' 🎉' if progress >= 100 else ''}
                    </div>
                </div>
                """
                st.markdown(html, unsafe_allow_html=True)
            else:
                st.markdown(
                    f'<div class="card"><div class="card-header">{r["ticker"]}</div>'
                    f'<div style="font-size:12px;color:#4a5568;">無固定止盈目標</div></div>',
                    unsafe_allow_html=True,
                )

    st.markdown("---")

    # ── Legend & Notes ──────────────────────────────────────────────────────
    with st.expander("📖 圖例與說明"):
        st.markdown("""
| 標記 | 含義 |
|------|------|
| 🔴 觸及止損 | 收盤價已跌破止損位 → **建議立即減倉或止損** |
| 🟡 接近止損 | 距止損不足 2% → 提高警惕，準備執行 |
| 🟢 接近止盈 | 距 TP1 不足 3% → 考慮分批鎖利 |
| ⚪ 安全 | 距止損/止盈尚遠，正常持有 |
| 🚀 強勢上升 | 股價遠高於所有 EMA，跟蹤止損有效 |
| 📈 緩步上升 | 股價高於短期 EMA |
| 🔄 回調中 | 從高位回調但仍高於長期 EMA |
| ⬇️ 下跌趨勢 | 股價跌破所有 EMA → 硬止損為主 |

**止損策略說明**:
- **硬止損** → 收盤跌破即執行，不猶豫
- **EMA 跟蹤止損** → 每日收盤後 EMA 值自動更新，止損線隨之上移
- **ATR 止損** → 倍數越大容錯空間越大，高波動股用大倍數

**每次更新**: 頁面刷新時自動獲取最新股價，止損/止盈配置由 3 年歷史回測決定
""")
