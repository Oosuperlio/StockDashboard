#!/usr/bin/env python3
"""
stop_monitor.py — Dynamic Stop-Loss & Take-Profit Monitoring Script

Checks all portfolio positions against their configured stop/TP levels
and outputs a formatted alert report. Designed for:
  1. Standalone CLI checks
  2. Cron job integration (no_agent=True — stdout is the report)
  3. Morning report inclusion

Usage:
  python3 scripts/stop_monitor.py
  python3 scripts/stop_monitor.py --silent-if-safe   # only output on alerts
"""

import json
import os
import sys
from datetime import datetime

import yfinance as yf
import pandas as pd

# ─── Paths ────────────────────────────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "data", "dynamic_stops", "stop_config.json")


# ─── Load Config ──────────────────────────────────────────────────────────────

def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return json.load(f)


from typing import Optional


def fetch_live_prices(tickers: list[str]) -> dict[str, Optional[float]]:
    """Fetch latest prices from yfinance, return {ticker: price}."""
    try:
        data = yf.download(tickers, period="1d", auto_adjust=True, progress=False)
        close = data["Close"] if hasattr(data, "columns") and isinstance(data.columns, pd.MultiIndex) else data
        prices = {}
        for tk in tickers:
            try:
                v = close[tk].iloc[-1] if isinstance(close, pd.DataFrame) else close.iloc[-1]
                prices[tk] = round(float(v), 2)
            except Exception:
                prices[tk] = None
        return prices
    except Exception as e:
        print(f"⚠️ Error fetching prices: {e}", file=sys.stderr)
        return {}


def check_positions(config: dict, silent_if_safe: bool = False) -> str:
    """Check all positions and return a formatted report string."""
    tickers_data = config.get("tickers", {})
    if not tickers_data:
        return "⚠️ 沒有止損配置數據"

    tickers = list(tickers_data.keys())
    live_prices = fetch_live_prices(tickers)

    lines = []
    alerts = {"breached": [], "near_stop": [], "near_tp": [], "approaching_tp": []}

    for tk in tickers:
        cfg = tickers_data[tk]
        price = live_prices.get(tk) or cfg.get("current_price", 0)
        if price is None:
            continue

        sl = cfg["stop_loss"]
        stop_price = sl["price"]
        tp_tiers = cfg["take_profit"]["tiers"]
        tp1_price = tp_tiers[0]["price"] if tp_tiers else None

        dist_to_stop = (price - stop_price) / price * 100
        dist_to_tp1 = ((tp1_price - price) / price * 100) if tp1_price and price > 0 else None

        # Determine alert level
        if price <= stop_price:
            alerts["breached"].append((tk, price, stop_price, dist_to_stop, tp1_price, dist_to_tp1, cfg))
        elif dist_to_stop <= 2.0:
            alerts["near_stop"].append((tk, price, stop_price, dist_to_stop, tp1_price, dist_to_tp1, cfg))
        elif tp1_price and price >= tp1_price:
            alerts["approaching_tp"].append((tk, price, stop_price, dist_to_stop, tp1_price, dist_to_tp1, cfg))
        elif tp1_price and dist_to_tp1 and dist_to_tp1 <= 3.0:
            alerts["near_tp"].append((tk, price, stop_price, dist_to_stop, tp1_price, dist_to_tp1, cfg))

    total_problems = sum(len(v) for v in alerts.values())

    if silent_if_safe and total_problems == 0:
        return ""

    # ── Build Report ───────────────────────────────────────────────────────
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M HKT")
    lines.append(f"🛡️ MMBH 止損監控報告 | {now_str}")
    lines.append(f"{'=' * 50}")
    lines.append(f"持倉數量: {len(tickers)} | 警報: {total_problems}")
    lines.append("")

    # 1. Breached / Near Stop
    if alerts["breached"] or alerts["near_stop"]:
        lines.append("🔴 止損警報")
        lines.append("-" * 40)

        for tk, price, stop_price, dist, tp1, tp1_dist, cfg in alerts["breached"]:
            trailing = cfg.get("trailing_stop", {})
            trailing_info = f" | 追蹤: {trailing.get('method', '—')}"
            if trailing.get("current_level"):
                ema_val = trailing["current_level"]
                ema_diff = (price - ema_val) / price * 100 if ema_val else None
                ema_str = f" | EMA: ${ema_val:.1f} ({ema_diff:+.1f}%)" if ema_diff else ""
            else:
                ema_str = ""

            lines.append(f"  {tk} — ⚠️ 跌破止損 ${stop_price:.2f} ! ")
            lines.append(f"     現價 ${price:.2f} (低於止損 {abs(dist):.1f}%){trailing_info}{ema_str}")
            lines.append(f"     建議: 立即執行止損")

        for tk, price, stop_price, dist, tp1, tp1_dist, cfg in alerts["near_stop"]:
            lines.append(f"  {tk} — 🟡 距止損僅 {dist:.1f}% ")
            lines.append(f"     現價 ${price:.2f} | 止損 ${stop_price:.2f}")
            tp1_str = f" | TP1 ${tp1:.2f} ({tp1_dist:+.1f}%)" if tp1 and tp1_dist else ""
            lines.append(f"     建議: 準備執行止損{tp1_str}")

    # 2. Take-profit alerts
    if alerts["approaching_tp"] or alerts["near_tp"]:
        lines.append("")
        lines.append("🟢 止盈警報")
        lines.append("-" * 40)

        for tk, price, stop_price, dist, tp1, tp1_dist, cfg in alerts["approaching_tp"]:
            lines.append(f"  {tk} — 🎉 觸及 TP1 ${tp1:.2f} ! ")
            lines.append(f"     現價 ${price:.2f} (超 TP1 {(price/tp1-1)*100:+.1f}%)")
            tp2 = cfg["take_profit"]["tiers"][1]["price"] if len(cfg["take_profit"]["tiers"]) > 1 else None
            if tp2:
                lines.append(f"     下一目標 TP2 ${tp2:.2f} ({((tp2/price)-1)*100:+.1f}%)")
            lines.append(f"     建議: 考慮分批鎖利 (TP1 30%)")

        for tk, price, stop_price, dist, tp1, tp1_dist, cfg in alerts["near_tp"]:
            lines.append(f"  {tk} — 🟢 接近 TP1 ${tp1:.2f} (距 {tp1_dist:.1f}%)")
            lines.append(f"     現價 ${price:.2f} | 止損 ${stop_price:.2f} ({dist:+.1f}%)")
            lines.append(f"     建議: 準備分批鎖利")

    # 3. Safe positions summary
    safe_count = len(tickers) - total_problems
    if safe_count > 0:
        lines.append("")
        lines.append("⚪ 安全持倉")
        lines.append("-" * 40)
        for tk in tickers:
            cfg = tickers_data[tk]
            price = live_prices.get(tk) or cfg.get("current_price", 0)
            sl = cfg["stop_loss"]
            stop_price = sl["price"]
            dist = (price - stop_price) / price * 100 if price > 0 else 0

            # Check if this stock is in any alert category
            is_alerted = any(
                tk == a[0]
                for alerts_list in alerts.values()
                for a in alerts_list
            )
            if not is_alerted:
                tp1_price = cfg["take_profit"]["tiers"][0]["price"] if cfg["take_profit"]["tiers"] else None
                tp1_str = f" | TP1 ${tp1_price:.2f}" if tp1_price else ""
                trailing = cfg.get("trailing_stop", {})
                trail_str = f" | 追蹤: {'✅' if trailing.get('current_level') else '❌'}"
                lines.append(f"  {tk}  ${price:.2f}  🛑${stop_price:.2f} ({dist:+.1f}%){tp1_str}{trail_str}")

    # 4. Stop-level comparison table
    lines.append("")
    lines.append("📊 距止損水平一覽")
    lines.append("-" * 40)
    for tk in tickers:
        cfg = tickers_data[tk]
        price = live_prices.get(tk) or cfg.get("current_price", 0)
        sl = cfg["stop_loss"]
        stop_price = sl["price"]
        dist = (price - stop_price) / price * 100 if price > 0 else 0
        bar = "█" * max(1, min(20, int(abs(dist) * 3))) if dist <= 5 else "█" * 20
        marker = "🔴" if dist <= 0 else "🟡" if dist <= 2 else "🟢" if dist <= 5 else "⚪"
        lines.append(f"  {tk:>5}  {marker}  ${price:>7.2f}  |{bar}|  ${stop_price:<7.2f}  ({dist:>+5.1f}%)")

    # 5. Action summary
    lines.append("")
    lines.append("📋 操作建議")
    lines.append("-" * 40)
    if alerts["breached"]:
        lines.append(f"  🔴 立即止損: {', '.join(a[0] for a in alerts['breached'])}")
    if alerts["near_stop"]:
        lines.append(f"  🟡 提高警覺: {', '.join(a[0] for a in alerts['near_stop'])}")
    if alerts["approaching_tp"]:
        lines.append(f"  🟢 考慮鎖利: {', '.join(a[0] for a in alerts['approaching_tp'])}")
    if total_problems == 0:
        lines.append("  所有持倉安全，無需操作 ✅")

    lines.append("")
    lines.append(f"{'=' * 50}")
    lines.append(f"報告生成: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | 數據: yfinance 即時")

    return "\n".join(lines)


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    silent_if_safe = "--silent-if-safe" in sys.argv

    try:
        config = load_config()
        report = check_positions(config, silent_if_safe=silent_if_safe)
        if report:
            print(report)
    except FileNotFoundError:
        print(f"⚠️ 找不到 {CONFIG_PATH}")
        print("請先執行回測腳本生成 stop_config.json")
        sys.exit(1)
    except Exception as e:
        print(f"⚠️ 執行錯誤: {e}", file=sys.stderr)
        sys.exit(1)
