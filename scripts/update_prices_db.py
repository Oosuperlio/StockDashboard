#!/usr/bin/env python3
"""
update_prices_db.py — Update prices.ddb with latest 5 trading days from yfinance.

Run before signal_scanner.py to ensure fresh data for all stocks.
Processes tickers in batches to avoid yfinance rate limits.
"""
import sys
import os
import logging
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import yfinance as yf
import duckdb
import pandas as pd
import numpy as np
import time

PRICES_DB = os.path.join(PROJECT_ROOT, "data", "prices.ddb")
BATCH_SIZE = 50
BATCH_DELAY = 2  # seconds between batches
LOOKBACK_DAYS = 10


def get_all_tickers(conn) -> list:
    """Get all tickers from prices.ddb."""
    rows = conn.execute(
        "SELECT DISTINCT symbol FROM stock_prices ORDER BY symbol"
    ).fetchall()
    return [r[0] for r in rows]


def get_latest_dates(conn) -> dict:
    """Return {ticker: latest_trade_date}."""
    rows = conn.execute(
        "SELECT symbol, MAX(trade_date) FROM stock_prices GROUP BY symbol"
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def update_tickers(tickers: list, conn) -> int:
    """Fetch latest data for tickers from yfinance and update DB. Returns count of updated rows."""
    if not tickers:
        return 0

    today = datetime.now().date()
    updated_rows = 0

    for i in range(0, len(tickers), BATCH_SIZE):
        batch = tickers[i:i + BATCH_SIZE]
        log.info("Batch %d/%d: %s …", i // BATCH_SIZE + 1,
                  (len(tickers) + BATCH_SIZE - 1) // BATCH_SIZE, batch[0])

        try:
            # Fetch just enough data: latest LOOKBACK_DAYS days
            start = today - timedelta(days=LOOKBACK_DAYS)
            data = yf.download(batch, start=start, end=today + timedelta(days=1),
                               auto_adjust=True, progress=False, group_by='ticker')

            if data.empty:
                log.info("  No new data for this batch")
                time.sleep(BATCH_DELAY)
                continue

            # Process each ticker in the batch
            for tk in batch:
                try:
                    # Parse the ticker's data
                    if len(batch) == 1:
                        df = data.copy()
                    else:
                        df = data[tk].copy() if tk in data.columns.get_level_values(0) else pd.DataFrame()

                    if df.empty:
                        continue

                    # Handle MultiIndex columns
                    if isinstance(df.columns, pd.MultiIndex):
                        df.columns = [c[0] for c in df.columns]

                    df = df.reset_index()
                    if 'Date' in df.columns:
                        df.rename(columns={'Date': 'trade_date'}, inplace=True)

                    currency = 'HKD' if tk.endswith('.HK') else 'USD'

                    for _, row in df.iterrows():
                        d = row['trade_date']
                        if hasattr(d, 'date'):
                            d = d.date()
                        elif hasattr(d, 'strftime'):
                            d = datetime.strptime(str(d)[:10], '%Y-%m-%d').date()

                        conn.execute("""
                            INSERT OR REPLACE INTO stock_prices
                            (trade_date, symbol, open, high, low, "close", volume, currency)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            d, tk,
                            float(row.get('Open', 0) or 0),
                            float(row.get('High', 0) or 0),
                            float(row.get('Low', 0) or 0),
                            float(row.get('Close', 0) or 0),
                            int(float(row.get('Volume', 0) or 0)),
                            currency
                        ))
                        updated_rows += 1

                except Exception as e:
                    log.debug("  %s: skipped (%s)", tk, e)

        except Exception as e:
            log.warning("Batch failed: %s", e)

        time.sleep(BATCH_DELAY)

    return updated_rows


def main():
    if not os.path.exists(PRICES_DB):
        log.error("prices.ddb not found at %s", PRICES_DB)
        return 1

    conn = duckdb.connect(PRICES_DB)
    try:
        tickers = get_all_tickers(conn)
        log.info("Found %d tickers in prices.ddb", len(tickers))

        latest_dates = get_latest_dates(conn)
        today = datetime.now().date()

        # Only fetch tickers whose latest data is older than yesterday
        # (skip fully up-to-date ones)
        stale = [t for t in tickers
                 if latest_dates.get(t) is None or latest_dates[t] < today - timedelta(days=1)]

        if not stale:
            log.info("All tickers are up to date!")
            return 0

        log.info("%d tickers need update (latest < %s)", len(stale), today - timedelta(days=1))

        conn.execute("BEGIN TRANSACTION")
        updated = update_tickers(stale, conn)
        conn.execute("COMMIT")

        log.info("Updated %d rows across %d tickers", updated, len(stale))
        print(f"📡 prices.ddb 更新完成: {updated} 行, {len(stale)} 檔股票")

    finally:
        conn.close()

    return 0


if __name__ == '__main__':
    sys.exit(main())
