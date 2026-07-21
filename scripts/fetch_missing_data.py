#!/usr/bin/env python3
"""Fetch missing market data individually."""
import yfinance as yf

tickers = {
    "^FTSE": "FTSE 100",
    "^N225": "Nikkei 225", 
    "^HSI": "Hang Seng",
    "000300.SS": "CSI 300",
    "USDHKD=X": "USD/HKD",
    "GC=F": "黃金",
    "CL=F": "WTI原油"
}

for ticker, name in tickers.items():
    try:
        data = yf.download(ticker, period="5d", interval="1d", progress=False, auto_adjust=True)
        if 'Close' in data.columns:
            vals = data['Close'].dropna()
            if len(vals) >= 2:
                cur = float(vals.iloc[-1])
                prev = float(vals.iloc[-2])
                chg = ((cur - prev) / prev) * 100
                print(f"{name} ({ticker}): {cur:,.2f} ({chg:+.2f}%)")
            elif len(vals) == 1:
                cur = float(vals.iloc[-1])
                print(f"{name} ({ticker}): {cur:,.2f}")
            else:
                print(f"{name} ({ticker}): No data")
        else:
            print(f"{name} ({ticker}): No 'Close' column")
            print(f"  Columns: {list(data.columns)}")
    except Exception as e:
        print(f"{name} ({ticker}): Error - {e}")
