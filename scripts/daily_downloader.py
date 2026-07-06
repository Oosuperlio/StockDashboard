#!/usr/bin/env python3
"""
Daily stock data downloader using yfinance, writes to DuckDB.
Run via daily_downloader.sh which uses the Hermes venv Python 3.11.
"""
import sys
import os
import logging
from datetime import datetime, timedelta

# Suppress yfinance's noisy error logs (e.g. "possibly delisted" on weekends)
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
logging.getLogger("yfinance").propagate = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ── Resolve project root ──────────────────────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import yfinance as yf
import duckdb
import pandas as pd
import numpy as np

# ── Config ────────────────────────────────────────────────────────────
STOCKS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "JPM",
    "V", "JNJ", "WMT", "PG", "MA", "UNH", "HD", "DIS", "BAC", "NFLX",
    "ADBE", "CRM", "INTC", "AMD", "PYPL", "NKE", "CMCSA", "KO", "PEP",
    "MRK", "ABBV", "TMO", "AVGO", "QCOM", "TXN", "COST", "ORCL", "ABT",
    "DHR", "ACN", "LIN", "CVX", "WFC", "MS", "C", "AXP", "IBM", "CAT",
    "GE", "MCD", "BA", "MMM",
    # Hong Kong stocks
    "0005.HK", "0700.HK", "9988.HK", "3690.HK", "1810.HK",
    "1299.HK", "0001.HK", "0011.HK", "0016.HK", "0012.HK",
]
DB_PATH = os.path.join(PROJECT_ROOT, "data", "market_data.duckdb")

def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = duckdb.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS daily_prices (
            ticker    VARCHAR,
            date      DATE,
            open      DOUBLE,
            high      DOUBLE,
            low       DOUBLE,
            close     DOUBLE,
            adj_close DOUBLE,
            volume    BIGINT,
            PRIMARY KEY (ticker, date)
        )
    """)
    return con

def get_latest_dates(con):
    """Return dict of {ticker: latest_date}."""
    rows = con.execute("SELECT ticker, MAX(date) FROM daily_prices GROUP BY ticker").fetchall()
    return {r[0]: r[1] for r in rows}

def normalize_columns(df, ticker):
    """Normalize yfinance DataFrame to consistent column names."""
    # Flatten MultiIndex columns → use the first level (e.g. 'Adj Close', 'Close', etc.)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]

    # Make a clean copy with renamed columns
    df = df.reset_index()  # index 'Date' becomes a column

    # Build column rename map (handle various yfinance versions)
    col_map = {}
    for c in df.columns:
        c_lower = c.strip().lower()
        if c_lower in ("date", "index"):
            col_map[c] = "date"
        elif c_lower == "adj close":
            col_map[c] = "adj_close"
        elif c_lower in ("open", "high", "low", "close", "volume"):
            col_map[c] = c_lower
        # else: drop unknown columns

    df = df.rename(columns=col_map)
    df["ticker"] = ticker

    # Keep only known columns
    keep = ["date", "ticker", "open", "high", "low", "close", "adj_close", "volume"]
    keep = [c for c in keep if c in df.columns]
    return df[keep]

def download_stock(ticker, latest_date):
    """Download data from latest_date+1 to today."""
    if latest_date:
        start = latest_date + timedelta(days=1)
        end = datetime.now().date()
        if start >= end:
            return None  # up to date
    else:
        start = "2010-01-01"
        end = datetime.now().strftime("%Y-%m-%d")

    df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
    if df.empty:
        return None

    df = normalize_columns(df, ticker)
    return df

def main():
    con = get_db()
    latest_dates = get_latest_dates(con)
    log.info("Latest dates for %d known tickers", len(latest_dates))

    updated, skipped, failed = [], [], []
    for ticker in STOCKS:
        try:
            ld = latest_dates.get(ticker)
            df = download_stock(ticker, ld)
            if df is None or len(df) == 0:
                skipped.append(ticker)
                log.info("  %s: skipped (up to date)", ticker)
                continue

            con.execute("BEGIN TRANSACTION")
            for _, row in df.iterrows():
                d = row["date"]
                if hasattr(d, "date"):
                    d = d.date()
                elif hasattr(d, "strftime"):
                    d = d.strftime("%Y-%m-%d")
                con.execute("""
                    INSERT OR REPLACE INTO daily_prices
                    (ticker, date, open, high, low, close, adj_close, volume)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    row["ticker"], d,
                    float(row["open"]), float(row["high"]), float(row["low"]),
                    float(row["close"]),
                    float(row.get("adj_close", row["close"])),
                    int(float(row["volume"]))
                ))
            con.execute("COMMIT")
            updated.append(ticker)
            log.info("  %s: updated %d rows", ticker, len(df))
        except Exception as e:
            try:
                con.execute("ROLLBACK")
            except Exception:
                pass
            failed.append((ticker, str(e)))
            log.error("  %s: FAILED - %s", ticker, e)

    # Get overall latest date
    row = con.execute("SELECT MAX(date) FROM daily_prices").fetchone()
    latest_overall = str(row[0]) if row and row[0] else "N/A"
    con.close()

    # ── Summary ────────────────────────────────────────────────────
    now_str = datetime.now().strftime("%Y-%m-%d")
    lines = [f"📡 每日數據更新 | {now_str}"]
    lines.append(f"- 更新: {len(updated)} 檔")
    lines.append(f"- 跳過: {len(skipped)} 檔（已是最新）")
    lines.append(f"- 失敗: {len(failed)} 檔")
    lines.append(f"- 最新數據日期: {latest_overall}")
    if failed:
        lines.append("")
        lines.append("❌ 失敗股票：")
        for t, err in failed:
            lines.append(f"  • {t}: {err}")
    report = "\n".join(lines)
    print(report)

    # Write report to a file
    report_path = os.path.join(PROJECT_ROOT, "data", "last_update_report.txt")
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w") as f:
        f.write(report)

    return 0 if not failed else 1

if __name__ == "__main__":
    sys.exit(main())
