#!/usr/bin/env python3
"""Fetch all portfolio prices, market indices, FX, commodities for MMBH morning report."""
import json, sys, os
import pandas as pd
import yfinance as yf

# ---------- Open positions (from portfolio.json analysis) ----------
# Format: ticker: (shares, avg_cost, stop_loss)
positions = {
    "ACN":  (200, 127.23, 135.00),
    "CBRE": (200, 134.64, 135.00),
    "CME":  (160, 233.31, 235.00),
    "CRWD": (240, 178.75, 179.50),
    "MPWR": (30,  1346.07, 1276.50),
    "SSNC": (600, 64.65,  65.00),
    "VTRS": (1300, 15.96,  15.50),
    "SNX":  (170, 252.97, 245.00),
    "WCC":  (130, 323.96, 320.00),
    "DASH": (210, 193.51, None),
}

# Target prices (from stop monitor data)
targets = {
    "ACN":  (147.00, 165.00),
    "CBRE": (137.00, 155.00),
    "CME":  (254.00, 280.00),
    "CRWD": (214.00, 240.00),
    "MPWR": (1457.00, 1650.00),
    "SSNC": (71.00, 80.00),
    "VTRS": (18.00, 21.00),
    "SNX":  (265.00, 300.00),
    "WCC":  (349.00, 380.00),
    "DASH": (215.00, 250.00),
}

# ---------- Market tickers ----------
tickers_str = (
    " ".join(positions.keys())
    + " ^GSPC ^IXIC ^DJI ^FTSE ^N225 ^HSI 000300.SS USDHKD=X GC=F CL=F"
)

print(f"Fetching {tickers_str}...", file=sys.stderr)

data = yf.download(tickers_str, period="5d", interval="1d", group_by="ticker", auto_adjust=True)

print(f"Columns: {data.columns}", file=sys.stderr)
print(f"Index (dates): {data.index.tolist()}", file=sys.stderr)

results = {}

# Handle multi-level columns
for ticker in list(positions.keys()) + ["^GSPC", "^IXIC", "^DJI", "^FTSE", "^N225", "^HSI", "000300.SS", "USDHKD=X", "GC=F", "CL=F"]:
    try:
        if isinstance(data.columns, pd.MultiIndex):
            ticker_data = data.xs(ticker, level=0, axis=1) if ticker in data.columns.get_level_values(0) else None
        else:
            ticker_data = data[ticker] if ticker in data.columns else None
        
        if ticker_data is not None and not ticker_data.empty:
            # Get the last 2 close prices
            closes = ticker_data["Close"].dropna()
            if len(closes) >= 2:
                latest = float(closes.iloc[-1])
                prev = float(closes.iloc[-2])
                change_pct = ((latest - prev) / prev) * 100
                results[ticker] = {"price": latest, "prev_close": prev, "change_pct": round(change_pct, 2)}
            elif len(closes) >= 1:
                results[ticker] = {"price": float(closes.iloc[-1]), "prev_close": None, "change_pct": 0}
        else:
            print(f"  No data for {ticker}", file=sys.stderr)
    except Exception as e:
        print(f"  Error for {ticker}: {e}", file=sys.stderr)

print(json.dumps(results, indent=2))
