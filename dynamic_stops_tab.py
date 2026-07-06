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

    # ── Overview cards (using native st.metric) ──────────────────────────────
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        safe_count = sum(1 for r in rows if r["status"] == "safe")
        safe_color = "🟢" if safe_count == len(rows) else "🟡" if safe_count > 0 else "🔴"
        st.metric(f"{safe_color} 安全持倉", f"{safe_count}/{len(rows)}")

    with col2:
        breached_count = alerts["breached"]
        st.metric("🔴 觸及止損", breached_count, delta_color="inverse")

    with col3:
        near_stop_count = alerts["near_stop"]
        st.metric("🟡 接近止損", near_stop_count, delta_color="inverse")

    with col4:
        st.metric("🟢 接近止盈", alerts["near_tp"])

    st.markdown("---")

    # ── Detailed monitoring table (using st.components.v1.html for reliable rendering) ──
    st.markdown("### 📋 持倉止損監控明細")

    # Sort: breached first, then near_stop, then near_tp, then safe
    status_order = {"breached_stop": 0, "near_stop": 1, "near_tp": 2, "approaching_tp": 3, "safe": 4, "unknown": 5}
    df["_sort"] = df["status"].map(lambda s: status_order.get(s, 5))
    df = df.sort_values("_sort").drop(columns=["_sort"])

    # Build HTML table (self-contained with inline styles)
    table_rows_html = ""
    for _, r in df.iterrows():
        row_class = ""
        if r["status"] == "breached_stop":
            row_class = ' class="stop-breach"'
        elif r["status"] == "near_stop":
            row_class = ' class="stop-near"'
        elif r["status"] in ("near_tp", "approaching_tp"):
            row_class = ' class="stop-tp-near"'

        # Dist to stop color
        d = r["dist_to_stop"]
        dist_stop_color = "#fc8181" if d < 3 else "#ecc94b" if d < 5 else "#48bb78"

        # Trailing info
        if r["trailing_active"] and r["trailing_level"]:
            trailing_html = f'<span class="tag tag-trail">EMA ${r["trailing_level"]:.2f}</span>'
        elif r["trailing_active"]:
            trailing_html = f'<span class="tag tag-trail">{r["trailing_method"][:12]}…</span>'
        else:
            trailing_html = '<span style="color:#4a5568;">—</span>'

        # Status badge
        if r["status"] == "breached_stop":
            badge = '<span class="tag tag-danger">⚠️ 觸及止損</span>'
        elif r["status"] == "near_stop":
            badge = f'<span class="tag tag-warn">距離 {d:.1f}%</span>'
        elif r["status"] in ("near_tp", "approaching_tp"):
            badge = '<span class="tag tag-tp">接近止盈</span>'
        else:
            badge = '<span class="tag tag-safe">安全</span>'

        tp1_str = f"${r['tp1_price']:.2f} ({r['tp1_pct']:+.1f}%)" if r["tp1_price"] and r["tp1_pct"] else ("—" if not r["tp1_price"] else f"${r['tp1_price']:.2f}")
        tp2_str = f"${r['tp2_price']:.2f} ({r['tp2_pct']:+.1f}%)" if r["tp2_price"] and r["tp2_pct"] else ("—" if not r["tp2_price"] else f"${r['tp2_price']:.2f}")
        dist_tp1_str = f"{r['dist_to_tp1']:.1f}%" if r["dist_to_tp1"] is not None else "—"

        table_rows_html += f"""
        <tr{row_class}>
            <td><b>{r['ticker']}</b><br><span style="font-size:11px;color:#718096;">{str(r['name'])[:18]}</span></td>
            <td>{r['trend_emoji']} {r['trend_label']}</td>
            <td style="font-weight:600;color:#e2e8f0;">${r['price']:.2f}</td>
            <td><span class="tag tag-sl">${r['stop_price']:.2f} ({r['stop_pct']:+.1f}%)</span></td>
            <td style="color:{dist_stop_color};font-weight:600;">{d:.1f}%</td>
            <td>{trailing_html}</td>
            <td><span class="tag tag-tp">{tp1_str}</span></td>
            <td>{dist_tp1_str}</td>
            <td><span class="tag tag-tp">{tp2_str}</span></td>
            <td style="font-size:11px;color:#718096;">{r['atr_pct']:.1f}% ×{r['atr_mult']:.1f}</td>
            <td>{badge}</td>
        </tr>"""

    full_table_html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
body {{ margin:0; padding:0; background:transparent; font-family:-apple-system,BlinkMacSystemFont,sans-serif; }}
.stop-table {{ width:100%; border-collapse:collapse; font-size:13px; }}
.stop-table th {{ background:#1e2533; color:#718096; font-weight:600;
    padding:10px 8px; text-align:left; border-bottom:2px solid #2d3748;
    font-size:11px; letter-spacing:0.05em; text-transform:uppercase; }}
.stop-table td {{ padding:10px 8px; border-bottom:1px solid #1a2035;
    color:#cbd5e0; vertical-align:middle; }}
.stop-table tr:hover td {{ background:rgba(255,255,255,0.02); }}
.stop-breach {{ background:rgba(252,129,129,0.08) !important; }}
.stop-near {{ background:rgba(236,201,75,0.08) !important; }}
.stop-tp-near {{ background:rgba(72,187,120,0.08) !important; }}
.tag {{ display:inline-block; padding:2px 8px; border-radius:4px;
    font-size:11px; font-weight:600; }}
.tag-sl {{ background:rgba(252,129,129,0.15); color:#fc8181; }}
.tag-tp {{ background:rgba(72,187,120,0.15); color:#48bb78; }}
.tag-trail {{ background:rgba(107,203,255,0.15); color:#6bcbff; }}
.tag-safe {{ background:rgba(72,187,120,0.1); color:#48bb78; }}
.tag-warn {{ background:rgba(236,201,75,0.15); color:#ecc94b; }}
.tag-danger {{ background:rgba(252,129,129,0.2); color:#fc8181; }}
</style></head><body>
<table class="stop-table">
<thead><tr>
    <th>股票</th><th>趨勢</th><th>現價</th><th>🛑 止損</th>
    <th>距止損</th><th>📉 追蹤</th><th>🎯 TP1</th>
    <th>距TP1</th><th>🎯 TP2</th><th>ATR</th><th>狀態</th>
</tr></thead>
<tbody>
{table_rows_html}
</tbody></table>
</body></html>"""

    st.components.v1.html(full_table_html, height=400, scrolling=True)

    st.markdown("---")

    # ── Take-profit progress bars (native Streamlit components) ──────────────
    st.markdown("### 📊 距目標價進度條")

    cols = st.columns(3)
    for i, r in enumerate(df.to_dict("records")):
        with cols[i % 3]:
            with st.container(border=True):
                st.markdown(f"**{r['ticker']}** → ${r['tp1_price']:.2f}" if r["tp1_price"] else f"**{r['ticker']}**")

                if r["tp1_price"] and r["price"] > 0:
                    # Calculate how far along to TP1
                    stop = r["stop_price"]
                    tp1 = r["tp1_price"]
                    if tp1 > stop:
                        progress = (r["price"] - stop) / (tp1 - stop) * 100
                    else:
                        progress = 50.0
                    progress = max(0, min(100, progress))

                    st.progress(progress / 100.0, text=f"${r['stop_price']:.1f} → ${tp1:.1f}")

                    # Color indicator
                    if progress >= 100:
                        st.success(f"🎉 **{progress:.0f}%** 完成 — 已達 TP1！")
                    elif progress >= 66:
                        st.info(f"**{progress:.0f}%** 完成")
                    elif progress >= 33:
                        st.warning(f"**{progress:.0f}%** 完成")
                    else:
                        st.write(f"**{progress:.0f}%** 完成")
                else:
                    st.caption("無固定止盈目標")
