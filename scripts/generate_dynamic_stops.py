#!/usr/bin/env python3
"""
generate_dynamic_stops.py — Update stop_config.json with current portfolio tickers.

Fetches live data (prices, ATR, EMAs) for ALL active portfolio tickers
and generates consolidated stop-loss & take-profit levels using the
existing methodology from the 7 original tickers.
"""

import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd
import yfinance as yf

# ── Paths ──────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "data", "dynamic_stops", "stop_config.json")

# All current portfolio holdings (from Railway portfolio.json)
TICKERS = ["ACN", "CBRE", "CME", "CRWD", "MPWR", "SSNC", "VTRS", "SNX", "WCC"]

# ── Helpers ────────────────────────────────────────────────────────────────

def fetch_analysis(ticker: str) -> dict | None:
    """
    Download 1y of daily data, compute ATR(14) and EMAs, 
    determine trend, and generate stop/TP levels.
    """
    try:
        df = yf.download(ticker, period="1y", auto_adjust=True, progress=False)
    except Exception as e:
        print(f"  ✗ Download failed for {ticker}: {e}", file=sys.stderr)
        return None

    if df.empty or len(df) < 20:
        print(f"  ✗ Insufficient data for {ticker}", file=sys.stderr)
        return None

    # Flatten MultiIndex columns if needed
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    
    df = df.copy()
    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)

    # EMAs
    ema20 = close.ewm(span=20, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    ema200 = close.ewm(span=200, adjust=False).mean()

    # ATR
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()

    last = df.iloc[-1]
    cp = float(close.iloc[-1])
    atr_val = float(atr.iloc[-1])
    atr_pct = round(atr_val / cp * 100, 1) if cp > 0 else 0
    ema20_v = float(ema20.iloc[-1])
    ema50_v = float(ema50.iloc[-1]) if not pd.isna(ema50.iloc[-1]) else None
    ema200_v = float(ema200.iloc[-1]) if not pd.isna(ema200.iloc[-1]) else None

    # Trend determination (same logic as original)
    above_ema20 = cp > ema20_v
    above_ema50 = ema50_v is not None and cp > ema50_v
    above_ema200 = ema200_v is not None and cp > ema200_v

    if above_ema20 and above_ema50 and above_ema200:
        # Check how far above
        if ema200_v and cp > ema200_v * 1.2:
            trend = "strong_uptrend"
            trend_detail = f"Far above all EMAs (EMA20=${ema20_v:.0f}, EMA50=${ema50_v:.0f}, EMA200=${ema200_v:.0f})"
        else:
            trend = "uptrend"
            trend_detail = f"Above EMA20(${ema20_v:.0f}) & EMA50(${ema50_v:.0f}), near EMA200(${ema200_v:.0f})" if ema50_v and ema200_v else f"Above EMA20(${ema20_v:.0f})"
    elif above_ema20 and above_ema50 and not above_ema200:
        trend = "pullback_in_uptrend"
        trend_detail = f"Below EMA20(${ema20_v:.0f}) & EMA50(${ema50_v:.0f}), still above EMA200(${ema200_v:.0f})" if ema200_v else f"Below EMAs"
    elif above_ema20 and not above_ema50:
        trend = "weak"
        trend_detail = f"At EMA20(${ema20_v:.0f}), below EMA50(${ema50_v:.0f}) and EMA200(${ema200_v:.0f})" if ema50_v and ema200_v else f"Weak"
    else:
        trend = "downtrend"
        trend_detail = f"Below all EMAs (EMA20=${ema20_v:.0f}, EMA50=${ema50_v:.0f}, EMA200=${ema200_v:.0f})" if ema50_v and ema200_v else f"Below EMA20(${ema20_v:.0f})"

    # Integer levels
    base = round(cp / 5) * 5
    supports_int = sorted([base - i * 5 for i in range(1, 6) if base - i * 5 < cp - 1], reverse=True)[:3]
    resists_int = sorted([base + i * 5 for i in range(1, 6) if base + i * 5 > cp + 1])[:3]

    # ── Stop-loss: ATR-based ──
    if trend in ("strong_uptrend", "uptrend"):
        mult = 2.0
        best_ema = "B_EMA50" if trend == "strong_uptrend" else "C_EMA200"
    elif trend == "pullback_in_uptrend":
        mult = 1.0
        best_ema = "A_EMA20"
    else:  # downtrend, weak
        mult = 1.0
        best_ema = "C_EMA200"

    stop_raw = cp - atr_val * mult
    # Round to nearest integer or 0.5
    stop_rounded = round(stop_raw * 2) / 2
    # Check if there's a nearby integer support
    for s in supports_int[:2]:
        if abs(stop_rounded - s) / cp < 0.03:  # within 3%
            stop_rounded = float(s)
            break

    stop_price = round(stop_rounded, 2)
    stop_pct = round((stop_price - cp) / cp * 100, 1)

    # ── Trailing stop ──
    if trend in ("strong_uptrend", "uptrend"):
        trailing_func = "EMA(50) trailing"
        if trend == "uptrend":
            trailing_func = "EMA(200) trailing"
        trailing_level = round(ema50_v, 2) if ema50_v and trend == "strong_uptrend" else (round(ema200_v, 2) if ema200_v else None)
        trailing_trigger = None
        trailing_note = f"Use {trailing_func} as primary exit signal. Hard stop as backup below EMA20."
    elif trend == "pullback_in_uptrend":
        trailing_func = f"EMA(20) after reclaiming ${ema20_v:.0f}"
        trailing_level = None
        trailing_trigger = round(ema20_v, 2)
        trailing_note = f"Activates when price reclaims EMA20 at ${ema20_v:.0f}. Until then, hold with hard stop."
    else:
        trailing_func = f"EMA(20) after reclaiming support"
        trailing_level = None
        trailing_trigger = round(cp * 1.03, 0) if cp < 50 else round(cp * 1.05, 0)
        trailing_note = f"Not active in {trend}. Activates when price recovers above ${trailing_trigger:.0f}."

    # ── Take-profit tiers ──
    if trend in ("strong_uptrend", "uptrend"):
        tp1 = round(cp * 1.08, 0)  # 8% up
        tp2 = round(cp * 1.15, 0)  # 15% up
        tp_tiers = [
            {"level": "TP1", "price": tp1, "pct_from_price": round((tp1-cp)/cp*100, 1), "allocation": "30%", "source": "trend extension"},
            {"level": "TP2", "price": tp2, "pct_from_price": round((tp2-cp)/cp*100, 1), "allocation": "30%", "source": "previous high + integer"},
            {"level": "TP3", "price": None, "pct_from_price": None, "allocation": "40%", "source": f"{trailing_func} - let winners run"}
        ]
    elif trend == "pullback_in_uptrend":
        tp1 = round(ema20_v, 0)  # recovery to EMA20
        tp2 = round(tp1 * 1.05, 0)
        tp_tiers = [
            {"level": "TP1", "price": tp1, "pct_from_price": round((tp1-cp)/cp*100, 1), "source": "EMA20 recovery target", "allocation": "30%"},
            {"level": "TP2", "price": tp2, "pct_from_price": round((tp2-cp)/cp*100, 1), "source": "integer resistance", "allocation": "30%"},
            {"level": "TP3", "price": None, "pct_from_price": None, "allocation": "40%", "source": "trend trailing after reclaim"}
        ]
    else:  # downtrend / weak
        tp1 = round(cp * 1.06, 0)
        tp2 = round(cp * 1.10, 0)
        tp_tiers = [
            {"level": "TP1", "price": tp1, "pct_from_price": round((tp1-cp)/cp*100, 1), "allocation": "30%", "source": "integer R1"},
            {"level": "TP2", "price": tp2, "pct_from_price": round((tp2-cp)/cp*100, 1), "allocation": "30%", "source": "integer R2"},
            {"level": "TP3", "price": None, "pct_from_price": None, "allocation": "40%", "source": f"EMA trailing after recovery"}
        ]

    # Integer key levels
    key_supports = [{"price": float(s), "source": "integer"} for s in supports_int]
    key_resistances = [{"price": float(r), "source": "integer"} for r in resists_int]

    return {
        "name": ticker,  # yfinance doesn't give name in fast mode; we'll fill later
        "current_price": round(cp, 2),
        "trend": trend,
        "trend_detail": trend_detail,
        "stop_loss": {
            "method": f"ATR {mult}x Hard Stop",
            "price": stop_price,
            "pct_from_price": stop_pct,
            "type": "hard",
            "logic": f"ATR(14)=${atr_val:.2f} → ${cp:.2f}-(${atr_val:.2f}×{mult})=${stop_raw:.2f}, rounded to ${stop_price}"
        },
        "trailing_stop": {
            "method": trailing_func,
            "current_level": trailing_level,
            "trigger_price": trailing_trigger,
            "note": trailing_note
        },
        "take_profit": {"tiers": tp_tiers},
        "atr": {"value": round(atr_val, 2), "pct": atr_pct, "period": 14},
        "recommended_atr_multiplier": mult,
        "best_ema_scheme": best_ema,
        "key_supports": key_supports[:3],
        "key_resistances": key_resistances[:3],
    }


def main():
    print(f"{'='*60}")
    print(f"Dynamic Stop Config Generator — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}")
    print(f"Tickers: {', '.join(TICKERS)}")

    results = {}
    for tk in TICKERS:
        print(f"\n▶ {tk}")
        result = fetch_analysis(tk)
        if result:
            results[tk] = result
            print(f"  ✓ ${result['current_price']:.2f} | {result['trend']} | "
                  f"Stop ${result['stop_loss']['price']:.2f} ({result['stop_loss']['pct_from_price']:+.1f}%)")
        else:
            print(f"  ✗ Failed")

    if not results:
        print("\n✗ No data generated — aborting.")
        sys.exit(1)

    # Try to get company names
    try:
        names = {}
        info = yf.download(list(results.keys()), period="1d", auto_adjust=True, progress=False)
        # Use Ticker.info for names (slower but more reliable for names)
        from concurrent.futures import ThreadPoolExecutor, as_completed
        def get_name(tk):
            try:
                t = yf.Ticker(tk)
                return tk, t.info.get("shortName", tk)
            except:
                return tk, tk
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {pool.submit(get_name, tk): tk for tk in results}
            for f in as_completed(futures):
                tk, name = f.result()
                if tk in results:
                    results[tk]["name"] = name
    except Exception as e:
        print(f"  (Names fetch skipped: {e})")
        for tk in results:
            results[tk]["name"] = tk

    # Build config
    config = {
        "metadata": {
            "generated_at": datetime.now().strftime("%Y-%m-%d"),
            "description": "Consolidated stop-loss and take-profit configuration per ticker",
            "sources": ["yfinance live data (ATR+EMA+integer support analysis)"],
            "update_frequency": "weekly (re-run on portfolio changes)"
        },
        "tickers": results
    }

    # Write
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)

    print(f"\n{'='*60}")
    print(f"✓ Updated: {CONFIG_PATH}")
    print(f"  Tickers: {len(results)}/{len(TICKERS)}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
