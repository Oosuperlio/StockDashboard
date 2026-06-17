"""
Load daily signal results saved by signal_scanner.py --save
for consumption by the Railway Streamlit Dashboard.

Files are stored in data/signals/:
  - latest_signals_us.csv    (US stocks — symbols NOT ending in .HK)
  - latest_signals_hk.csv    (HK stocks — symbols ending in .HK)
  - latest_signals_all.csv   (combined)

These files are overwritten each time signal_scanner.py runs with --save.
"""

import os
from pathlib import Path
from typing import Optional

import pandas as pd

# ─── Path resolution ────────────────────────────────────────────────────────

SIGNALS_DIR = Path(__file__).resolve().parent.parent / "data" / "signals"


def load_daily_signals(
    market: Optional[str] = None,
    sort_by: tuple = ("tier", "win_rate"),
) -> pd.DataFrame:
    """
    Load the latest signal results.

    Parameters
    ----------
    market : str or None
        'us' — US stocks only (symbols without .HK suffix)
        'hk' — HK stocks only (symbols with .HK suffix)
        None — all signals combined

    sort_by : tuple
        Column(s) to sort by. Default: tier ascending, win_rate descending.

    Returns
    -------
    pd.DataFrame with columns:
        symbol, sector, subsector, indicator, signal, pattern,
        pattern_conf, confidence, win_rate, win_rate_stock, win_rate_sector,
        stock_n, sector_n, avg_return, price, tp1_price, tp2_price, sl_price,
        date, tier, volume_confirmed
    """
    fname = {
        "us": "latest_signals_us.csv",
        "hk": "latest_signals_hk.csv",
        None: "latest_signals_all.csv",
    }.get(market, "latest_signals_all.csv")

    path = SIGNALS_DIR / fname
    if not path.exists():
        return pd.DataFrame()

    try:
        df = pd.read_csv(path)
    except Exception:
        return pd.DataFrame()

    if df.empty:
        return df

    # Ensure tier is integer for proper sorting
    if "tier" in df.columns:
        df["tier"] = df["tier"].astype(int)

    # Sort: tier ascending, win_rate descending
    sort_cols = []
    ascending = []
    for col in sort_by:
        if col in df.columns:
            sort_cols.append(col)
            ascending.append(False if col in ("win_rate", "win_rate_stock", "win_rate_sector", "confidence") else True)

    if sort_cols:
        df = df.sort_values(sort_cols, ascending=ascending)

    return df.reset_index(drop=True)


def get_signal_summary(market: Optional[str] = None) -> dict:
    """
    Return a lightweight summary (tier counts) without loading the full DataFrame.
    """
    df = load_daily_signals(market)
    if df.empty:
        return {"total": 0, "tier_1": 0, "tier_2": 0, "tier_3": 0}

    return {
        "total": len(df),
        "tier_1": int((df["tier"] == 1).sum()),
        "tier_2": int((df["tier"] == 2).sum()),
        "tier_3": int((df["tier"] == 3).sum()),
    }


def signal_date() -> Optional[str]:
    """
    Return the date of the latest signal scan (from file mtime),
    or None if no signals found.
    """
    path = SIGNALS_DIR / "latest_signals_all.csv"
    if not path.exists():
        return None
    # Use file mtime — represents when the scan was last run
    from datetime import datetime
    mtime = os.path.getmtime(path)
    return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
