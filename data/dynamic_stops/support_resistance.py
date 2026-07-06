#!/usr/bin/env python3
"""
Workstream A — Support/Resistance Detection
Dynamically detects support & resistance levels for 7 US stocks using:
  - Pivot Points (local extrema with 5-10 bar confirmation)
  - Volume Profile (high-volume price clusters)
  - Integer Levels (round-number support/resistance)
  - Fibonacci Retracement (0.382/0.5/0.618 on last major swing)
"""

import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf
from scipy.signal import argrelextrema

warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=UserWarning)
# Suppress urllib3 OpenSSL warning — it's harmless
warnings.filterwarnings("ignore")

OUTPUT_PATH = Path.home() / "projects/dashboard/data/dynamic_stops/support_resistance.json"
TICKERS = ["ACN", "CBRE", "CME", "CRWD", "MPWR", "SSNC", "VTRS"]
START_DATE = "2023-07-05"
END_DATE = "2026-07-05"
PIVOT_ORDER = 7  # left/right bars for local extrema detection


def download_data(ticker):
    """Download OHLCV data from yfinance."""
    df = yf.download(ticker, start=START_DATE, end=END_DATE, auto_adjust=True, progress=False)
    if df.empty:
        print(f"WARNING: No data for {ticker}", file=sys.stderr)
        return None
    # Flatten multi-level columns if needed
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.columns = [c.lower() for c in df.columns]
    df.dropna(inplace=True)
    return df


def find_pivot_highs_lows(df, order=PIVOT_ORDER):
    """Find pivot highs and lows using scipy argrelextrema."""
    highs = df["high"].values
    lows = df["low"].values

    # Local maxima in highs
    local_max_idx = argrelextrema(highs, np.greater, order=order)[0]
    # Local minima in lows
    local_min_idx = argrelextrema(lows, np.less, order=order)[0]

    pivot_highs = df.iloc[local_max_idx][["high", "volume", "date"]].copy() if len(local_max_idx) else pd.DataFrame()
    pivot_lows = df.iloc[local_min_idx][["low", "volume", "date"]].copy() if len(local_min_idx) else pd.DataFrame()

    # Rename to 'price' for uniform handling
    if not pivot_highs.empty:
        pivot_highs = pivot_highs.rename(columns={"high": "price"})
    if not pivot_lows.empty:
        pivot_lows = pivot_lows.rename(columns={"low": "price"})

    return pivot_highs, pivot_lows


def detect_volume_profile(df, num_bins=20):
    """Find high-volume price clusters (volume profile)."""
    price_min = df["low"].min()
    price_max = df["high"].max()
    bins = np.linspace(price_min, price_max, num_bins + 1)
    df["price_bin"] = pd.cut((df["high"] + df["low"]) / 2, bins=bins, labels=False)

    vol_profile = df.groupby("price_bin").agg(
        total_volume=("volume", "sum"),
        mid_price=("close", "mean")
    ).reset_index()
    vol_profile["bin_low"] = bins[vol_profile["price_bin"]]
    vol_profile["bin_high"] = bins[vol_profile["price_bin"] + 1]

    # Sort by volume descending
    vol_profile.sort_values("total_volume", ascending=False, inplace=True)

    # Top 3 volume clusters
    top_clusters = vol_profile.head(3)

    clusters = []
    for _, row in top_clusters.iterrows():
        clusters.append({
            "low": round(row["bin_low"], 2),
            "high": round(row["bin_high"], 2),
            "volume": int(row["total_volume"]),
            "mid": round(row["mid_price"], 2),
        })

    return clusters


def find_integer_levels(current_price, df, num_levels=5):
    """Find nearest integer support/resistance levels."""
    # Find the rough price range
    price_range = (df["low"].min(), df["high"].max())

    # Generate integer levels around current price
    base = round(current_price / 5) * 5  # round to nearest 5
    all_levels = [base + i * 5 for i in range(-num_levels, num_levels + 1)]
    all_levels = [l for l in all_levels if price_range[0] <= l <= price_range[1] * 1.1]

    # Sort by distance to current price
    supports = sorted([l for l in all_levels if l < current_price], key=lambda x: current_price - x)
    resistances = sorted([l for l in all_levels if l > current_price])

    return supports[:3], resistances[:3]


def find_major_swing(df, order=3):
    """Find the most recent major swing using pivot points.
    Uses a smaller order (3) to find recent swings, then picks
    the last two adjacent pivot high and low."""
    highs = df["high"].values
    lows = df["low"].values

    # Find local extrema with smaller order for more sensitivity
    max_idx = argrelextrema(highs, np.greater, order=order)[0]
    min_idx = argrelextrema(lows, np.less, order=order)[0]

    # Filter to last ~200 bars
    cutoff = max(0, len(df) - 200)
    max_idx = [i for i in max_idx if i >= cutoff]
    min_idx = [i for i in min_idx if i >= cutoff]

    if len(max_idx) == 0 or len(min_idx) == 0:
        # Fallback: use overall min/max from last 126 bars
        last = df.tail(126)
        return last["low"].min(), last["high"].max()

    # Merge and sort all pivot indices chronologically
    pivots = [(i, "high", df.iloc[i]["high"]) for i in max_idx] + \
             [(i, "low", df.iloc[i]["low"]) for i in min_idx]
    pivots.sort(key=lambda x: x[0])

    # Take last 4 pivots (2 swings) and find the most recent complete swing
    recent = pivots[-4:] if len(pivots) >= 4 else pivots

    # Find alternating high/low pattern for the last complete swing
    swing_low = None
    swing_high = None
    for i in range(len(recent) - 1):
        p1_type, p1_val = recent[i][1], recent[i][2]
        p2_type, p2_val = recent[i + 1][1], recent[i + 1][2]
        if p1_type != p2_type:
            if p1_type == "low" and p2_type == "high":
                # Upward swing
                swing_low = p1_val
                swing_high = p2_val
            elif p1_type == "high" and p2_type == "low":
                # Downward swing
                swing_high = p1_val
                swing_low = p2_val

    if swing_low is not None and swing_high is not None:
        return swing_low, swing_high

    # Final fallback
    last = df.tail(126)
    return last["low"].min(), last["high"].max()


def fibonacci_levels(swing_low, swing_high):
    """Calculate Fibonacci retracement levels for a swing."""
    diff = swing_high - swing_low
    levels = {
        "波段低點": round(swing_low, 2),
        "波段高點": round(swing_high, 2),
        "0.236": round(swing_high - 0.236 * diff, 2),
        "0.382": round(swing_high - 0.382 * diff, 2),
        "0.500": round(swing_high - 0.500 * diff, 2),
        "0.618": round(swing_high - 0.618 * diff, 2),
        "0.786": round(swing_high - 0.786 * diff, 2),
        "1.000": round(swing_low, 2),
        "1.272": round(swing_high + 0.272 * diff, 2),
        "1.382": round(swing_high + 0.382 * diff, 2),
        "1.618": round(swing_high + 0.618 * diff, 2),
    }
    return levels


def analyze_stock(ticker):
    """Run full support/resistance analysis for one stock."""
    print(f"  Analyzing {ticker}...")
    df = download_data(ticker)
    if df is None:
        return None

    df.reset_index(inplace=True)
    if "date" not in df.columns and "Date" in [c.capitalize() for c in df.columns]:
        df.rename(columns={c: c.lower() for c in df.columns}, inplace=True)
    # Ensure date column exists
    if "date" not in df.columns:
        # Try to find the date column
        for col in df.columns:
            if "date" in col.lower() or "time" in col.lower():
                df.rename(columns={col: "date"}, inplace=True)
                break
        else:
            # If no date column, just use index
            df["date"] = df.index

    current_price = round(df["close"].iloc[-1], 2)
    print(f"    Current price: ${current_price}")

    # ---- Pivot Points ----
    pivot_highs, pivot_lows = find_pivot_highs_lows(df)

    # Get most recent significant pivots (near current price, from last year)
    recent_df = df.tail(252)  # last year of trading
    recent_highs = pivot_highs[pivot_highs["price"].isin(recent_df["high"])] if not pivot_highs.empty else pd.DataFrame()
    recent_lows = pivot_lows[pivot_lows["price"].isin(recent_df["low"])] if not pivot_lows.empty else pd.DataFrame()

    # Get strongest pivot levels
    # For support: pivot lows below current price, sorted by strength (volume)
    supports_from_pivots = []
    if not recent_lows.empty:
        candidates = recent_lows[recent_lows["price"] < current_price].copy()
        candidates["strength"] = candidates["price"].rank(pct=True)
        candidates = candidates.sort_values("price", ascending=False)
        for _, row in candidates.head(3).iterrows():
            supports_from_pivots.append({
                "price": round(row["price"], 2),
                "volume": int(row.get("volume", 0)),
                "type": "pivot_low"
            })

    resistances_from_pivots = []
    if not recent_highs.empty:
        candidates = recent_highs[recent_highs["price"] > current_price].copy()
        candidates = candidates.sort_values("price", ascending=True)
        for _, row in candidates.head(3).iterrows():
            resistances_from_pivots.append({
                "price": round(row["price"], 2),
                "volume": int(row.get("volume", 0)),
                "type": "pivot_high"
            })

    print(f"    Pivot highs found: {len(pivot_highs)}, Pivot lows found: {len(pivot_lows)}")

    # ---- Volume Profile ----
    vol_clusters = detect_volume_profile(df)
    print(f"    Top volume clusters: {[c['mid'] for c in vol_clusters]}")

    # ---- Integer Levels ----
    int_supports, int_resistances = find_integer_levels(current_price, df)
    print(f"    Integer supports: {int_supports}, resistances: {int_resistances}")

    # ---- Fibonacci ----
    swing_low, swing_high = find_major_swing(df)
    fib = fibonacci_levels(swing_low, swing_high)
    print(f"    Swing: ${swing_low:.2f} → ${swing_high:.2f}")

    # ---- Build structured output ----
    # Compile support levels (S1, S2, S3) — only levels BELOW current price
    compiled_supports = []
    compiled_resistances = []

    # Add pivot supports
    for s in supports_from_pivots:
        if s["price"] < current_price:
            compiled_supports.append({
                "price": s["price"],
                "label": f"前低樞紐點 (Vol:{s['volume']:,})",
                "source": "pivot"
            })

    # Add volume profile supports (clusters below current price)
    for vc in vol_clusters:
        if vc["mid"] < current_price:
            compiled_supports.append({
                "price": vc["mid"],
                "label": f"成交量密集區 (${vc['low']:.2f}-${vc['high']:.2f})",
                "source": "volume_profile"
            })

    # Add integer supports (already < current_price from find_integer_levels)
    for il in int_supports:
        compiled_supports.append({
            "price": il,
            "label": f"整數關口",
            "source": "integer"
        })

    # Add Fibonacci support levels (retracements below current price)
    fib_support_keys = ["0.618", "0.500", "0.382"]
    for k in fib_support_keys:
        if fib[k] < current_price:
            compiled_supports.append({
                "price": fib[k],
                "label": f"斐波那契 {k} 回調",
                "source": "fibonacci"
            })

    # ---- Compile resistance levels (only levels ABOVE current price) ----
    for r in resistances_from_pivots:
        if r["price"] > current_price:
            compiled_resistances.append({
                "price": r["price"],
                "label": f"前高樞紐點 (Vol:{r['volume']:,})",
                "source": "pivot"
            })

    for vc in vol_clusters:
        if vc["mid"] > current_price:
            compiled_resistances.append({
                "price": vc["mid"],
                "label": f"成交量密集區 (${vc['low']:.2f}-${vc['high']:.2f})",
                "source": "volume_profile"
            })

    for il in int_resistances:
        compiled_resistances.append({
            "price": il,
            "label": f"整數關口",
            "source": "integer"
        })

    # Add Fibonacci extension levels (above current price)
    fib_resist_keys = ["1.272", "1.382", "1.618"]
    for k in fib_resist_keys:
        if fib[k] > current_price:
            compiled_resistances.append({
                "price": fib[k],
                "label": f"斐波那契 {k} 延伸",
                "source": "fibonacci"
            })

    # Deduplicate and sort
    def dedup_and_sort(levels, ascending=True):
        seen = set()
        unique = []
        for l in levels:
            key = round(l["price"], 1)
            if key not in seen and l["price"] > 0:
                seen.add(key)
                unique.append(l)
        unique.sort(key=lambda x: x["price"], reverse=not ascending)
        return unique[:5]  # top 5 unique levels

    compiled_supports = dedup_and_sort(compiled_supports, ascending=False)  # highest support first
    compiled_resistances = dedup_and_sort(compiled_resistances, ascending=True)  # lowest resistance first

    # Assign S1/S2/S3 and R1/R2/R3
    def label_levels(levels, prefix):
        result = []
        for i, l in enumerate(levels[:3]):
            result.append({
                "level": f"{prefix}{i + 1}",
                "price": l["price"],
                "label": l["label"],
                "source": l["source"]
            })
        return result

    result = {
        "ticker": ticker,
        "current_price": current_price,
        "supports": label_levels(compiled_supports, "S"),
        "resistances": label_levels(compiled_resistances, "R"),
        "fibonacci_reference": {
            "swing_low": fib["波段低點"],
            "swing_high": fib["波段高點"],
            "0.382": fib["0.382"],
            "0.500": fib["0.500"],
            "0.618": fib["0.618"],
            "1.272": fib["1.272"],
            "1.382": fib["1.382"],
            "1.618": fib["1.618"],
        },
        "volume_clusters": vol_clusters,
        "pivot_high_count": len(pivot_highs),
        "pivot_low_count": len(pivot_lows),
        "data_points": len(df),
        "date_range": {
            "start": str(df["date"].iloc[0].date()) if hasattr(df["date"].iloc[0], "date") else str(df["date"].iloc[0]),
            "end": str(df["date"].iloc[-1].date()) if hasattr(df["date"].iloc[-1], "date") else str(df["date"].iloc[-1]),
        }
    }

    return result


def format_output(result):
    """Format the analysis result as a human-readable string."""
    t = result["ticker"]
    cp = result["current_price"]
    lines = [
        f"股票: {t}",
        f"當前價: ${cp:.2f}",
        "",
        "--- 支撐位 ---",
    ]

    for s in result["supports"]:
        lines.append(f"{s['level']}: ${s['price']:.2f} ({s['label']})")

    lines.append("")
    lines.append("--- 阻力位 ---")
    for r in result["resistances"]:
        lines.append(f"{r['level']}: ${r['price']:.2f} ({r['label']})")

    fib = result["fibonacci_reference"]
    lines.append("")
    lines.append(f"--- 斐波那契參考（最近波段）---")
    lines.append(f"波段: ${fib['swing_low']:.2f} → ${fib['swing_high']:.2f}")
    for k in ["0.382", "0.500", "0.618"]:
        lines.append(f"{k}: ${fib[k]:.2f}")

    return "\n".join(lines)


def main():
    print("=" * 60)
    print("Workstream A — Support/Resistance Detection")
    print("=" * 60)
    print(f"Stocks: {', '.join(TICKERS)}")
    print(f"Period: {START_DATE} → {END_DATE}")
    print(f"Pivot order: {PIVOT_ORDER} bars each side")
    print()

    all_results = {}
    for ticker in TICKERS:
        print(f"\n{'─' * 50}")
        print(f"▶ {ticker}")
        print(f"{'─' * 50}")
        try:
            result = analyze_stock(ticker)
            if result:
                all_results[ticker] = result
                print()
                print(format_output(result))
            else:
                print(f"  ✗ Failed to analyze {ticker}")
        except Exception as e:
            print(f"  ✗ Error analyzing {ticker}: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc()

    # Save JSON output
    output = {
        "metadata": {
            "generated_at": pd.Timestamp.now().isoformat(),
            "period": {"start": START_DATE, "end": END_DATE},
            "stocks": TICKERS,
            "method": "Pivot Points + Volume Profile + Integer Levels + Fibonacci",
            "pivot_order": PIVOT_ORDER,
        },
        "results": all_results,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False, default=str)

    print(f"\n{'=' * 60}")
    print(f"✓ Results saved to {OUTPUT_PATH}")
    print(f"  Stocks analyzed: {len(all_results)}/{len(TICKERS)}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
