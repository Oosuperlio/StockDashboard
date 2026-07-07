#!/usr/bin/env python3
"""
Daily stock data downloader using yfinance, writes to DuckDB.
Run via daily_downloader.sh.
Downloads ALL tickers from prices.ddb (signal_scanner universe) + dashboard STOCKS.
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
import time

# ── Config ────────────────────────────────────────────────────────────
CORE_STOCKS = [
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
PRICES_DB = os.path.join(PROJECT_ROOT, "data", "prices.ddb")
BATCH_SIZE = 100  # yfinance batch download size
BATCH_DELAY = 1   # seconds between batches


# ─── Database helpers ─────────────────────────────────────────────────

def get_market_db():
    """Open market_data.duckdb, ensure daily_prices table exists."""
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


def get_prices_db():
    """Open prices.ddb, ensure stock_prices table exists."""
    os.makedirs(os.path.dirname(PRICES_DB), exist_ok=True)
    con = duckdb.connect(PRICES_DB)
    con.execute("""
        CREATE TABLE IF NOT EXISTS stock_prices (
            trade_date DATE, symbol VARCHAR,
            open DECIMAL(12,4), high DECIMAL(12,4), low DECIMAL(12,4),
            "close" DECIMAL(12,4), volume BIGINT,
            currency VARCHAR,
            fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (symbol, trade_date)
        )
    """)
    return con


def load_all_tickers() -> list:
    """
    Return the full list of tickers to download:
    – CORE_STOCKS (always included)
    – All tickers from prices.ddb (signal_scanner universe)
    """
    tickers = set(CORE_STOCKS)
    if os.path.exists(PRICES_DB):
        try:
            con = duckdb.connect(PRICES_DB)
            rows = con.execute(
                "SELECT DISTINCT symbol FROM stock_prices ORDER BY symbol"
            ).fetchall()
            con.close()
            for r in rows:
                tickers.add(r[0])
            log.info("Loaded %d tickers from prices.ddb (total unique: %d)",
                     len(rows), len(tickers))
        except Exception as e:
            log.warning("Could not read prices.ddb tickers: %s", e)
    return sorted(tickers)


def get_latest_market_dates(con):
    """Return dict of {ticker: latest_date} from daily_prices."""
    rows = con.execute(
        "SELECT ticker, MAX(date) FROM daily_prices GROUP BY ticker"
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def get_latest_prices_dates(con):
    """Return dict of {symbol: latest_trade_date} from stock_prices."""
    rows = con.execute(
        "SELECT symbol, MAX(trade_date) FROM stock_prices GROUP BY symbol"
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def normalize_columns(df, ticker):
    """Normalize yfinance DataFrame to consistent column names."""
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]

    df = df.reset_index()
    col_map = {}
    for c in df.columns:
        c_lower = c.strip().lower()
        if c_lower in ("date", "index"):
            col_map[c] = "date"
        elif c_lower == "adj close":
            col_map[c] = "adj_close"
        elif c_lower in ("open", "high", "low", "close", "volume"):
            col_map[c] = c_lower

    df = df.rename(columns=col_map)
    df["ticker"] = ticker
    keep = [c for c in ["date", "ticker", "open", "high", "low", "close", "adj_close", "volume"]
            if c in df.columns]
    return df[keep]


def download_batch(tickers: list) -> dict:
    """
    Download a batch of tickers from yfinance.
    Returns {ticker: pd.DataFrame} with normalized data.
    """
    today = datetime.now().date()
    start = today - timedelta(days=10)  # Fetch last ~7 trading days
    end = today + timedelta(days=1)     # yfinance end is exclusive

    try:
        data = yf.download(tickers, start=start, end=end,
                           auto_adjust=True, progress=False, group_by='ticker')
    except Exception as e:
        log.warning("Batch download failed: %s", e)
        return {}

    if data.empty:
        return {}

    results = {}
    for tk in tickers:
        try:
            if len(tickers) == 1:
                df = data.copy()
            else:
                if tk not in data.columns.get_level_values(0):
                    continue
                df = data[tk].copy()

            if df.empty or len(df) == 0:
                continue

            df = normalize_columns(df, tk)
            results[tk] = df
        except Exception:
            continue

    return results


def write_to_both_dbs(mkt_con, prices_con, ticker: str, df: pd.DataFrame):
    """Write the same data to both market_data.duckdb and prices.ddb."""
    currency = 'HKD' if ticker.endswith('.HK') else 'USD'

    # Write to market_data.duckdb (daily_prices)
    mkt_con.execute("BEGIN TRANSACTION")
    try:
        for _, row in df.iterrows():
            d = row["date"]
            if hasattr(d, "date"):
                d = d.date()
            elif hasattr(d, "strftime"):
                d = d.strftime("%Y-%m-%d")
            mkt_con.execute("""
                INSERT OR REPLACE INTO daily_prices
                (ticker, date, open, high, low, close, adj_close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                row["ticker"], d,
                float(row["open"]), float(row["high"]), float(row["low"]),
                float(row["close"]),
                float(row.get("adj_close", row["close"])),
                int(float(row["volume"])) if not np.isnan(float(row["volume"])) else 0
            ))
        mkt_con.execute("COMMIT")
    except Exception:
        mkt_con.execute("ROLLBACK")
        raise

    # Write to prices.ddb (stock_prices)
    prices_con.execute("BEGIN TRANSACTION")
    try:
        for _, row in df.iterrows():
            d = row["date"]
            if hasattr(d, "date"):
                d = d.date()
            elif hasattr(d, "strftime"):
                d = d.strftime("%Y-%m-%d")
            prices_con.execute("""
                INSERT OR REPLACE INTO stock_prices
                (trade_date, symbol, open, high, low, "close", volume, currency)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (d, ticker,
                  float(row["open"]), float(row["high"]), float(row["low"]),
                  float(row["close"]),
                  int(float(row["volume"])) if not np.isnan(float(row["volume"])) else 0,
                  currency))
        prices_con.execute("COMMIT")
    except Exception:
        prices_con.execute("ROLLBACK")
        raise


# ─── Main ─────────────────────────────────────────────────────────────

def main():
    # Build full ticker list
    all_tickers = load_all_tickers()
    log.info("Total unique tickers to check: %d", len(all_tickers))

    mkt_con = get_market_db()
    prices_con = get_prices_db()

    # Get latest dates from both DBs to skip up-to-date tickers
    mkt_dates = get_latest_market_dates(mkt_con)
    today = datetime.now().date()

    # Filter to tickers that might need updating
    stale = []
    for tk in all_tickers:
        ld = mkt_dates.get(tk)
        if ld is None or ld < today - timedelta(days=3):
            stale.append(tk)

    log.info("Stale tickers (no data or last >3 days ago): %d / %d",
             len(stale), len(all_tickers))

    if not stale:
        log.info("All tickers are up to date!")
        print(f"📡 每日數據更新 | {today}\n- 更新: 0 檔\n- 所有 {len(all_tickers)} 檔已是最新\n- 最新數據日期: {today}")
        mkt_con.close()
        prices_con.close()
        return 0

    # Download in batches
    updated, skipped, failed = [], [], []
    for i in range(0, len(stale), BATCH_SIZE):
        batch = stale[i:i + BATCH_SIZE]
        log.info("Batch %d/%d (%d tickers): %s …",
                 i // BATCH_SIZE + 1,
                 (len(stale) + BATCH_SIZE - 1) // BATCH_SIZE,
                 len(batch), batch[0])

        results = download_batch(batch)
        if not results:
            log.info("  No new data for this batch")
            time.sleep(BATCH_DELAY)
            continue

        for tk, df in results.items():
            try:
                write_to_both_dbs(mkt_con, prices_con, tk, df)
                updated.append(tk)
                log.info("  %s: updated %d rows", tk, len(df))
            except Exception as e:
                failed.append((tk, str(e)))
                log.error("  %s: FAILED - %s", tk, e)

        # Fallback: retry tickers missing from batch results (solo download)
        missing = [tk for tk in batch if tk not in results and tk not in failed]
        if missing:
            log.info("  %d tickers missing from batch, retrying individually…", len(missing))
            for tk in missing:
                try:
                    df = download_batch([tk])
                    if tk in df and not df[tk].empty:
                        write_to_both_dbs(mkt_con, prices_con, tk, df[tk])
                        updated.append(tk)
                        log.info("  %s: updated (solo retry)", tk)
                    else:
                        skipped.append(tk)
                except Exception as e:
                    skipped.append(tk)
                    log.debug("  %s: solo retry failed: %s", tk, e)

        # Tickers in batch that got no results are skipped
        for tk in batch:
            if tk not in results and tk not in failed:
                skipped.append(tk)
                log.info("  %s: skipped (no new data)", tk)

        time.sleep(BATCH_DELAY)

    # Summary
    mkt_con.close()
    prices_con.close()

    row = duckdb.connect(DB_PATH).execute(
        "SELECT MAX(date) FROM daily_prices"
    ).fetchone()
    latest_overall = str(row[0]) if row and row[0] else "N/A"

    now_str = datetime.now().strftime("%Y-%m-%d")
    lines = [f"📡 每日數據更新 | {now_str}"]
    lines.append(f"- 總檔數: {len(all_tickers)}")
    lines.append(f"- 更新: {len(updated)} 檔")
    lines.append(f"- 跳過: {len(skipped)} 檔（無新數據）")
    lines.append(f"- 失敗: {len(failed)} 檔")
    lines.append(f"- 最新數據日期: {latest_overall}")
    if failed:
        lines.append("")
        lines.append("❌ 失敗股票：")
        for t, err in failed[:10]:
            lines.append(f"  • {t}: {err}")
        if len(failed) > 10:
            lines.append(f"  … 還有 {len(failed) - 10} 檔")
    report = "\n".join(lines)
    print(report)

    report_path = os.path.join(PROJECT_ROOT, "data", "last_update_report.txt")
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w") as f:
        f.write(report)

    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
