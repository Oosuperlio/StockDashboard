#!/usr/bin/env python3
"""Fetch real-time prices for portfolio holdings and market indices."""
import json
import yfinance as yf
from datetime import datetime, timezone

HKT = timezone.utc  # We'll just use UTC and note it

# ========== PORTFOLIO ==========
with open("/Users/aiagent/projects/dashboard/data/portfolio/portfolio.json") as f:
    portfolio = json.load(f)

# Calculate open positions with remaining shares
open_positions = []
for pos in portfolio:
    ticker = pos["ticker"]
    total_shares = 0
    total_cost = 0.0
    total_sold_shares = 0
    total_sold_proceeds = 0.0
    avg_cost = 0.0
    
    buys = [t for t in pos["trades"] if t["type"] == "buy"]
    sells = [t for t in pos["trades"] if t["type"] == "sell"]
    
    for t in buys:
        total_shares += t["shares"]
        total_cost += t["shares"] * t["price"]
    
    if total_shares > 0:
        avg_cost = total_cost / total_shares
    
    for t in sells:
        total_sold_shares += t["shares"]
        total_sold_proceeds += t["shares"] * t["price"]
    
    remaining_shares = total_shares - total_sold_shares
    
    if remaining_shares > 0:
        # Cost basis for remaining shares (FIFO-ish: proportional)
        remaining_cost = avg_cost * remaining_shares
        open_positions.append({
            "ticker": ticker,
            "shares": remaining_shares,
            "avg_cost": round(avg_cost, 4),
            "total_cost": round(remaining_cost, 2)
        })

print(f"=== OPEN POSITIONS ({len(open_positions)}) ===")
for p in open_positions:
    print(f"{p['ticker']}: {p['shares']} sh @ ${p['avg_cost']:.4f} = ${p['total_cost']:.2f}")

# ========== FETCH PRICES ==========
stock_tickers = [p["ticker"] for p in open_positions]
index_tickers = ["^GSPC", "^IXIC", "^DJI", "^FTSE", "^N225", "^HSI", "000300.SS"]
forex_tickers = ["USDHKD=X"]
commodity_tickers = ["GC=F", "CL=F"]

all_tickers = stock_tickers + index_tickers + forex_tickers + commodity_tickers
print(f"\nFetching {len(all_tickers)} tickers...")

# Download in batches to avoid rate limiting
batch_size = 10
results = {}

for i in range(0, len(all_tickers), batch_size):
    batch = all_tickers[i:i+batch_size]
    try:
        data = yf.download(batch, period="2d", interval="1d", progress=False, auto_adjust=True)
        print(f"  Batch {i//batch_size +1}: got {len(batch)} tickers")
        
        for t in batch:
            try:
                if t in data.columns.levels[1] if hasattr(data.columns, 'levels') else False:
                    # Multi-level columns
                    close = data['Close'][t]
                    if len(close) >= 2:
                        current = float(close.iloc[-1])
                        prev_close = float(close.iloc[-2])
                    elif len(close) == 1:
                        current = float(close.iloc[-1])
                        prev_close = current
                    else:
                        current = prev_close = None
                else:
                    # Try single-level
                    close = data['Close'] if 'Close' in data.columns else None
                    if close is not None and t in close.columns:
                        c = close[t]
                        if len(c) >= 2:
                            current = float(c.iloc[-1])
                            prev_close = float(c.iloc[-2])
                        elif len(c) == 1:
                            current = float(c.iloc[-1])
                            prev_close = current
                        else:
                            current = prev_close = None
                    else:
                        current = prev_close = None
                
                if current is not None:
                    change_pct = ((current - prev_close) / prev_close) * 100 if prev_close and prev_close != 0 else 0
                    results[t] = {
                        "current": round(current, 2),
                        "prev_close": round(prev_close, 2) if prev_close else None,
                        "change_pct": round(change_pct, 2)
                    }
                else:
                    results[t] = {"current": None, "prev_close": None, "change_pct": 0}
            except Exception as e:
                print(f"    Error processing {t}: {e}")
                results[t] = {"current": None, "prev_close": None, "change_pct": 0}
                
    except Exception as e:
        print(f"  Batch {i//batch_size +1} failed: {e}")
        for t in batch:
            results[t] = {"current": None, "prev_close": None, "change_pct": 0}

# ========== PRINT RESULTS ==========
print(f"\n=== PORTFOLIO PRICES ({datetime.now().strftime('%Y-%m-%d %H:%M')}) ===")
total_cost_all = 0
total_market_value = 0
total_pl = 0

for p in open_positions:
    t = p["ticker"]
    r = results.get(t, {})
    cur = r.get("current")
    change = r.get("change_pct", 0)
    
    if cur:
        mkt_val = round(cur * p["shares"], 2)
        pl = round(mkt_val - p["total_cost"], 2)
        pl_pct = round((pl / p["total_cost"]) * 100, 2) if p["total_cost"] != 0 else 0
    else:
        mkt_val = 0
        pl = 0
        pl_pct = 0
    
    total_cost_all += p["total_cost"]
    total_market_value += mkt_val
    total_pl += pl
    
    print(f"{t} ({p['shares']} sh @ ${p['avg_cost']:.2f}):")
    print(f"  現價 ${cur:.2f}" if cur else "  現價 N/A")
    print(f"  成本 ${p['total_cost']:.2f} | 市值 ${mkt_val:.2f}")
    print(f"  盈虧 ${pl:+.2f} ({pl_pct:+.2f}%)")

print(f"\n=== TOTALS ===")
print(f"總成本: ${total_cost_all:.2f}")
print(f"總市值: ${total_market_value:.2f}")
print(f"總盈虧: ${total_pl:+.2f} ({(total_pl/total_cost_all)*100:+.2f}%)" if total_cost_all != 0 else "總盈虧: $0.00")

print(f"\n=== MARKET INDICES ===")
for t in index_tickers:
    r = results.get(t, {})
    cur = r.get("current")
    chg = r.get("change_pct", 0)
    name = {
        "^GSPC": "S&P 500", "^IXIC": "Nasdaq", "^DJI": "Dow Jones",
        "^FTSE": "FTSE 100", "^N225": "Nikkei 225", "^HSI": "Hang Seng",
        "000300.SS": "CSI 300"
    }.get(t, t)
    if cur:
        print(f"{name}: {cur:,.2f} ({chg:+.2f}%)")
    else:
        print(f"{name}: N/A")

print(f"\n=== FOREX ===")
for t in forex_tickers:
    r = results.get(t, {})
    cur = r.get("current")
    chg = r.get("change_pct", 0)
    name = {"USDHKD=X": "USD/HKD"}.get(t, t)
    if cur:
        print(f"{name}: {cur:.4f} ({chg:+.2f}%)")
    else:
        print(f"{name}: N/A")

print(f"\n=== COMMODITIES ===")
for t in commodity_tickers:
    r = results.get(t, {})
    cur = r.get("current")
    chg = r.get("change_pct", 0)
    name = {"GC=F": "黃金", "CL=F": "WTI原油"}.get(t, t)
    if cur:
        print(f"{name}: ${cur:.2f} ({chg:+.2f}%)")
    else:
        print(f"{name}: N/A")

# Save results for the report
output = {
    "positions": open_positions,
    "prices": {k: v for k, v in results.items() if k in stock_tickers},
    "indices": {k: v for k, v in results.items() if k in index_tickers},
    "forex": {k: v for k, v in results.items() if k in forex_tickers},
    "commodities": {k: v for k, v in results.items() if k in commodity_tickers},
    "totals": {
        "total_cost": round(total_cost_all, 2),
        "total_market_value": round(total_market_value, 2),
        "total_pl": round(total_pl, 2),
        "total_pl_pct": round((total_pl/total_cost_all)*100, 2) if total_cost_all != 0 else 0
    },
    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M")
}

with open("/Users/aiagent/projects/dashboard/data/daily_market_data.json", "w") as f:
    json.dump(output, f, indent=2, ensure_ascii=False)

print(f"\n=== Data saved to daily_market_data.json ===")
