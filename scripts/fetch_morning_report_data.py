#!/usr/bin/env python3
"""Fetch all data for the MMBH morning report."""
import json
import yfinance as yf
from datetime import datetime, timezone, timedelta
import warnings
warnings.filterwarnings('ignore')

# === CONFIG ===
HKT = timezone(timedelta(hours=8))
now_hkt = datetime.now(HKT)
date_str = now_hkt.strftime("%Y-%m-%d")

# Open positions from portfolio analysis
# CRWD: 240 bought @ $178.75, 80 sold @ $205.75 => 160 remaining, cost basis FIFO
positions = {
    "CME":  {"shares": 160, "cost_basis": 233.31},
    "CRWD": {"shares": 160, "cost_basis": 178.75},
    "MPWR": {"shares": 30,  "cost_basis": 1346.07},
    "SNX":  {"shares": 170, "cost_basis": 252.97},
    "WCC":  {"shares": 130, "cost_basis": 323.96},
    "DASH": {"shares": 210, "cost_basis": 193.51},
    "PNR":  {"shares": 550, "cost_basis": 76.84},
    "INVH": {"shares": 1400,"cost_basis": 30.01},
}

# Market tickers
tickers_list = {
    # Stocks
    "CME": "CME",
    "CRWD": "CRWD",
    "MPWR": "MPWR",
    "SNX": "SNX",
    "WCC": "WCC",
    "DASH": "DASH",
    "PNR": "PNR",
    "INVH": "INVH",
    # Indices
    "^GSPC": "S&P 500",
    "^IXIC": "Nasdaq",
    "^DJI": "Dow Jones",
    "^FTSE": "FTSE 100",
    "^N225": "Nikkei 225",
    "^HSI": "Hang Seng",
    "000300.SS": "CSI 300",
    # FX
    "USDHKD=X": "USD/HKD",
    # Commodities
    "GC=F": "Gold",
    "CL=F": "WTI Crude",
}

ticker_symbols = list(tickers_list.keys())

print(f"📡 Fetching data for {date_str}...", flush=True)

# Fetch all data at once
data = yf.download(
    ticker_symbols,
    period="5d",
    interval="1d",
    group_by="ticker",
    auto_adjust=True,
    progress=False,
)

print(f"Downloaded data shape: {data.shape if hasattr(data, 'shape') else 'unknown'}", flush=True)

# The data structure depends on whether single or multiple tickers
# With multiple tickers, it returns a multi-level column DataFrame

# Process stock prices
results = {}

for symbol in ticker_symbols:
    try:
        if symbol in data.columns.levels[1] if hasattr(data.columns, 'levels') else False:
            # Multi-index columns
            close_data = data['Close'][symbol] if 'Close' in data.columns.get_level_values(0) else None
            if close_data is not None:
                close_vals = close_data.dropna()
                if len(close_vals) >= 2:
                    curr_price = float(close_vals.iloc[-1])
                    prev_close = float(close_vals.iloc[-2])
                    change_pct = ((curr_price - prev_close) / prev_close) * 100
                    results[symbol] = {
                        "name": tickers_list[symbol],
                        "price": curr_price,
                        "change_pct": round(change_pct, 2),
                    }
                elif len(close_vals) == 1:
                    curr_price = float(close_vals.iloc[-1])
                    results[symbol] = {
                        "name": tickers_list[symbol],
                        "price": curr_price,
                        "change_pct": 0.0,
                    }
    except Exception as e:
        print(f"  ⚠️ Error processing {symbol}: {e}", flush=True)

# If the above didn't work, try flat structure
if not results:
    try:
        for symbol in ticker_symbols:
            try:
                close_data = data['Close']
                if symbol in close_data.columns:
                    vals = close_data[symbol].dropna()
                    if len(vals) >= 2:
                        curr_price = float(vals.iloc[-1])
                        prev_close = float(vals.iloc[-2])
                        change_pct = ((curr_price - prev_close) / prev_close) * 100
                        results[symbol] = {
                            "name": tickers_list[symbol],
                            "price": curr_price,
                            "change_pct": round(change_pct, 2),
                        }
                    elif len(vals) == 1:
                        curr_price = float(vals.iloc[-1])
                        results[symbol] = {
                            "name": tickers_list[symbol],
                            "price": curr_price,
                            "change_pct": 0.0,
                        }
            except Exception as e:
                print(f"  ⚠️ Error (flat) {symbol}: {e}", flush=True)
    except Exception as e:
        print(f"  ⚠️ Flat structure error: {e}", flush=True)

# Fallback: use yfinance Ticker individually for any missing tickers
for symbol in ticker_symbols:
    if symbol not in results:
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="5d")
            if not hist.empty and len(hist) >= 2:
                curr_price = float(hist['Close'].iloc[-1])
                prev_close = float(hist['Close'].iloc[-2])
                change_pct = ((curr_price - prev_close) / prev_close) * 100
                results[symbol] = {
                    "name": tickers_list[symbol],
                    "price": curr_price,
                    "change_pct": round(change_pct, 2),
                }
            elif not hist.empty:
                curr_price = float(hist['Close'].iloc[-1])
                results[symbol] = {
                    "name": tickers_list[symbol],
                    "price": curr_price,
                    "change_pct": 0.0,
                }
        except Exception as e:
            print(f"  ❌ Failed {symbol}: {e}", flush=True)

print(f"\n✅ Fetched {len(results)}/{len(ticker_symbols)} tickers", flush=True)

# === Calculate portfolio P&L ===
portfolio = []
total_cost = 0
total_value = 0

for ticker, info in positions.items():
    symbol = ticker
    if symbol in results:
        price = results[symbol]["price"]
        cost = info["cost_basis"] * info["shares"]
        value = price * info["shares"]
        pl = value - cost
        pl_pct = ((price - info["cost_basis"]) / info["cost_basis"]) * 100
        
        total_cost += cost
        total_value += value
        
        portfolio.append({
            "ticker": ticker,
            "shares": info["shares"],
            "cost_basis": info["cost_basis"],
            "curr_price": round(price, 2),
            "cost": round(cost, 2),
            "value": round(value, 2),
            "pl": round(pl, 2),
            "pl_pct": round(pl_pct, 2),
        })
    else:
        print(f"  ⚠️ No price for {ticker}", flush=True)

total_pl = total_value - total_cost
total_pl_pct = ((total_value / total_cost) - 1) * 100 if total_cost > 0 else 0

print(f"\n💰 Portfolio Summary:", flush=True)
print(f"   Total Cost: ${total_cost:,.2f}", flush=True)
print(f"   Total Value: ${total_value:,.2f}", flush=True)
print(f"   Total P&L: ${total_pl:+,.2f} ({total_pl_pct:+.2f}%)", flush=True)

# === Output as JSON for the report generator ===
output = {
    "date": date_str,
    "portfolio": portfolio,
    "total_cost": round(total_cost, 2),
    "total_value": round(total_value, 2),
    "total_pl": round(total_pl, 2),
    "total_pl_pct": round(total_pl_pct, 2),
    "market_data": {},
}

for symbol, r in results.items():
    display_name = r["name"]
    output["market_data"][display_name] = {
        "symbol": symbol,
        "price": r["price"],
        "change_pct": r["change_pct"],
    }

with open("/tmp/morning_report_data.json", "w") as f:
    json.dump(output, f, indent=2, ensure_ascii=False)

print(f"\n✅ Data saved to /tmp/morning_report_data.json", flush=True)
