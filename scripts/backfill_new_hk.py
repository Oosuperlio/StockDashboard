"""Quick backfill: fetch 1 year of historical data for newly added HK stocks."""
import sys
from pathlib import Path

BASE_DIR = Path("/Users/aiagent/projects/dashboard")
sys.path.insert(0, str(BASE_DIR))

import yfinance as yf
import duckdb
from datetime import datetime, date
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

DB_PATH = BASE_DIR / "data" / "prices.ddb"
log = print

# Newly added HK stocks that need history
NEW_HK = [
    "0151.HK", "0241.HK", "0267.HK", "0293.HK", "0700.HK",
    "0762.HK", "1024.HK", "1088.HK", "1113.HK", "1658.HK",
    "1787.HK", "1876.HK", "1928.HK", "1997.HK", "2020.HK",
    "2269.HK", "2318.HK", "2388.HK", "3328.HK", "6618.HK",
    "9633.HK", "9961.HK", "9988.HK",
]

def get_conn():
    conn = duckdb.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stock_prices (
            trade_date  DATE,
            symbol      VARCHAR,
            open        DECIMAL(12,4),
            high        DECIMAL(12,4),
            low         DECIMAL(12,4),
            close       DECIMAL(12,4),
            volume      BIGINT,
            currency    VARCHAR,
            fetched_at  TIMESTAMP,
            PRIMARY KEY (trade_date, symbol)
        )
    """)
    return conn

def backfill_one(ticker):
    """Fetch 365 days of history and upsert into DuckDB."""
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="1y", auto_adjust=True)
        if hist.empty:
            log(f"  ❌ {ticker}: empty data")
            return 0
        
        # Determine currency from info
        currency = getattr(t, 'info', {}).get('currency', 'HKD')
        if currency == 'HKD':
            currency = 'HKD'
        else:
            currency = 'USD'
        
        hist = hist.reset_index()
        hist.columns = [c.lower().replace(' ', '_') for c in hist.columns]
        date_col = [c for c in hist.columns if 'date' in c.lower()][0]
        
        fetched_at = datetime.now()
        conn = get_conn()
        try:
            rows = []
            for _, row in hist.iterrows():
                d = row[date_col]
                if hasattr(d, 'date'):
                    d = d.date()
                rows.append({
                    'trade_date': d,
                    'symbol': ticker,
                    'open': float(row.get('open', 0)),
                    'high': float(row.get('high', 0)),
                    'low': float(row.get('low', 0)),
                    'close': float(row.get('close', 0)),
                    'volume': int(row.get('volume', 0)),
                    'currency': currency,
                    'fetched_at': fetched_at,
                })
            
            conn.executemany(
                """INSERT INTO stock_prices (trade_date, symbol, open, high, low, close, volume, currency, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (trade_date, symbol) DO UPDATE SET
                       open = excluded.open, high = excluded.high, low = excluded.low,
                       close = excluded.close, volume = excluded.volume, fetched_at = excluded.fetched_at""",
                [(r['trade_date'], r['symbol'], r['open'], r['high'], r['low'], r['close'], r['volume'], r['currency'], r['fetched_at']) for r in rows]
            )
            log(f"  ✅ {ticker}: {len(rows)} rows ({rows[0]['trade_date']} ~ {rows[-1]['trade_date']})")
            return len(rows)
        finally:
            conn.close()
    except Exception as e:
        log(f"  ❌ {ticker}: {str(e)[:80]}")
        return 0

def main():
    log(f"Backfilling {len(NEW_HK)} HK stocks...")
    total = 0
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(backfill_one, t): t for t in NEW_HK}
        for f in as_completed(futures):
            total += f.result()
    log(f"\nDone! Total rows added: {total}")

if __name__ == "__main__":
    main()
