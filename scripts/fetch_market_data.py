#!/usr/bin/env python3
"""Fetch market data and stock prices from Yahoo Finance v8 API."""
import json
import urllib.request
import urllib.error
import ssl
import sys

def fetch_yahoo_v8(symbol):
    """Fetch quote from Yahoo Finance v8 API."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=5d"
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        })
        with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        
        result = data['chart']['result'][0]
        meta = result['meta']
        
        # Get current price
        current_price = meta.get('regularMarketPrice')
        if current_price is None:
            current_price = meta.get('previousClose')
        
        # Get previous close for change calculation
        prev_close = meta.get('chartPreviousClose')
        if prev_close is None:
            prev_close = meta.get('previousClose')
        
        # Get timestamp
        timestamp = meta.get('regularMarketTime')
        
        # Get quotes data
        quotes = result.get('indicators', {}).get('quote', [{}])[0]
        closes = quotes.get('close', [])
        opens = quotes.get('open', [])
        
        return {
            'symbol': symbol,
            'current_price': current_price,
            'prev_close': prev_close,
            'timestamp': timestamp,
            'closes': closes if closes else [],
            'opens': opens if opens else []
        }
    except Exception as e:
        return {'symbol': symbol, 'error': str(e)}

# Current holdings
holdings = {
    'CME': 160,
    'CRWD': 160,
    'MPWR': 30,
    'SNX': 170,
    'WCC': 130,
    'DASH': 210,
    'PNR': 550,
    'INVH': 1400
}

# Cost basis
cost_basis = {
    'CME': 233.31,
    'CRWD': 178.75,  # Remaining 160 shares at original cost
    'MPWR': 1346.07,
    'SNX': 252.97,
    'WCC': 323.96,
    'DASH': 193.51,
    'PNR': 76.84,
    'INVH': 30.01
}

# Target prices (approximate analyst targets)
target_prices = {
    'CME': (240, 270),
    'CRWD': (200, 240),
    'MPWR': (1400, 1600),
    'SNX': (270, 300),
    'WCC': (340, 380),
    'DASH': (200, 240),
    'PNR': (82, 92),
    'INVH': (32, 36)
}

# Stop-loss levels (approx 8-10% below cost)
stop_loss = {
    'CME': round(233.31 * 0.92, 2),
    'CRWD': round(178.75 * 0.92, 2),
    'MPWR': round(1346.07 * 0.92, 2),
    'SNX': round(252.97 * 0.92, 2),
    'WCC': round(323.96 * 0.92, 2),
    'DASH': round(193.51 * 0.92, 2),
    'PNR': round(76.84 * 0.92, 2),
    'INVH': round(30.01 * 0.92, 2)
}

# Market indices and commodities
market_tickers = [
    '^GSPC', '^IXIC', '^DJI',
    '^FTSE', '^N225',
    '^HSI', '000300.SS',
    'USDHKD=X', 'GC=F', 'CL=F'
]

market_names = {
    '^GSPC': 'S&P 500',
    '^IXIC': 'NASDAQ',
    '^DJI': 'Dow Jones',
    '^FTSE': 'FTSE 100',
    '^N225': 'Nikkei 225',
    '^HSI': 'Hang Seng',
    '000300.SS': 'CSI 300',
    'USDHKD=X': 'USD/HKD',
    'GC=F': 'Gold',
    'CL=F': 'WTI Crude'
}

print("=== PORTFOLIO STOCK PRICES ===")
for sym in holdings:
    data = fetch_yahoo_v8(sym)
    if 'error' in data:
        print(f"ERROR|{sym}|{data['error']}")
    else:
        curr = data['current_price']
        prev = data['prev_close']
        change_pct = ((curr - prev) / prev * 100) if prev and curr else 0
        
        cost = cost_basis[sym]
        shares = holdings[sym]
        total_cost = cost * shares
        total_value = curr * shares if curr else 0
        pl_amount = total_value - total_cost
        pl_pct = ((curr - cost) / cost * 100) if cost and curr else 0
        
        target_low, target_high = target_prices[sym]
        dist_to_target = ((target_low - curr) / curr * 100) if curr else 0
        sl = stop_loss[sym]
        
        print(f"DATA|{sym}|{curr}|{prev}|{change_pct:.2f}|{cost}|{shares}|{total_cost}|{total_value}|{pl_amount}|{pl_pct:.2f}|{target_low}|{target_high}|{dist_to_target:.2f}|{sl}")

print("\n=== MARKET INDICES ===")
for sym in market_tickers:
    data = fetch_yahoo_v8(urllib.parse.quote(sym, safe=''))
    if 'error' in data:
        print(f"ERROR|{sym}|{data['error']}")
    else:
        curr = data['current_price']
        prev = data['prev_close']
        change_pct = ((curr - prev) / prev * 100) if prev and curr else 0
        name = market_names.get(sym, sym)
        print(f"DATA|{name}|{sym}|{curr}|{prev}|{change_pct:.2f}")

print("\n=== DONE ===")
