"""
DuckDB client for stock prices and financial metrics time-series storage.
"""

import duckdb
import os
from datetime import date, datetime, timedelta
from typing import Optional, List, Dict, Any

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
PRICES_DB = os.path.join(DATA_DIR, "prices.ddb")
FINANCIALS_DB = os.path.join(DATA_DIR, "financials.ddb")

os.makedirs(DATA_DIR, exist_ok=True)


def _get_prices_conn() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(PRICES_DB, read_only=False)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stock_prices (
            trade_date  DATE,
            symbol      VARCHAR(20),
            open        DECIMAL(12,4),
            high        DECIMAL(12,4),
            low         DECIMAL(12,4),
            close       DECIMAL(12,4),
            volume      BIGINT,
            currency    VARCHAR(5),
            fetched_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (symbol, trade_date)
        );
    """)
    # Auto-compact after writes
    conn.execute("PRAGMA threads=2;")
    return conn


def _get_financials_conn() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(FINANCIALS_DB, read_only=False)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS financial_metrics (
            symbol          VARCHAR(20),
            metric_date     DATE,
            pe_ratio        DECIMAL(10,3),
            eps             DECIMAL(10,3),
            dividend_yield  DECIMAL(8,4),
            roe             DECIMAL(8,4),
            market_cap      BIGINT,
            revenue         BIGINT,
            current_price   DECIMAL(10,3),
            target_price    DECIMAL(10,3),
            fetched_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (symbol, metric_date)
        );
    """)
    conn.execute("PRAGMA threads=2;")
    return conn


# ─── Price Operations ────────────────────────────────────────────────────────

def upsert_price(symbol: str, trade_date: date, open_: float, high: float,
                 low: float, close: float, volume: int, currency: str = "USD") -> None:
    """Insert or replace a daily OHLCV record."""
    conn = _get_prices_conn()
    try:
        conn.execute("""
            INSERT INTO stock_prices (trade_date, symbol, open, high, low, close, volume, currency)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, trade_date) DO UPDATE SET
                open = excluded.open,
                high = excluded.high,
                low  = excluded.low,
                close = excluded.close,
                volume = excluded.volume,
                currency = excluded.currency,
                fetched_at = excluded.fetched_at;
        """, [trade_date, symbol.upper(), open_, high, low, close, volume, currency])
    finally:
        conn.close()


def get_price_range(symbol: str, days: int = 90) -> List[Dict[str, Any]]:
    """Return price history for a symbol, newest first, up to `days` trading days."""
    conn = _get_prices_conn()
    try:
        rows = conn.execute("""
            SELECT trade_date, open, high, low, close, volume, currency
            FROM stock_prices
            WHERE symbol = ?
              AND trade_date >= CURRENT_DATE - INTERVAL '1 day' * ?
            ORDER BY trade_date DESC;
        """, [symbol.upper(), days]).fetchall()
        return [dict(zip(["trade_date","open","high","low","close","volume","currency"], r)) for r in rows]
    finally:
        conn.close()


def get_latest_price_date(symbol: str) -> Optional[date]:
    """Return the most recent trade_date we have for this symbol."""
    conn = _get_prices_conn()
    try:
        row = conn.execute("""
            SELECT MAX(trade_date) FROM stock_prices WHERE symbol = ?;
        """, [symbol.upper()]).fetchone()
        return row[0] if row and row[0] else None
    finally:
        conn.close()


def is_price_stale(symbol: str, max_age_days: int = 1) -> bool:
    """Return True if we don't have fresh price data for this symbol."""
    latest = get_latest_price_date(symbol)
    if latest is None:
        return True
    return (date.today() - latest) > timedelta(days=max_age_days)


# ─── Financial Metrics Operations ────────────────────────────────────────────

def upsert_financial(symbol: str, metric_date: date, metrics: Dict[str, Any]) -> None:
    """Insert or replace a financial metrics snapshot."""
    conn = _get_financials_conn()
    try:
        conn.execute("""
            INSERT INTO financial_metrics (
                symbol, metric_date, pe_ratio, eps, dividend_yield, roe,
                market_cap, revenue, current_price, target_price
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, metric_date) DO UPDATE SET
                pe_ratio = excluded.pe_ratio,
                eps = excluded.eps,
                dividend_yield = excluded.dividend_yield,
                roe = excluded.roe,
                market_cap = excluded.market_cap,
                revenue = excluded.revenue,
                current_price = excluded.current_price,
                target_price = excluded.target_price,
                fetched_at = excluded.fetched_at;
        """, [
            symbol.upper(),
            metric_date,
            metrics.get("pe_ratio"),
            metrics.get("eps"),
            metrics.get("dividend_yield"),
            metrics.get("roe"),
            metrics.get("market_cap"),
            metrics.get("revenue"),
            metrics.get("current_price"),
            metrics.get("target_price"),
        ])
    finally:
        conn.close()


def get_latest_financial(symbol: str) -> Optional[Dict[str, Any]]:
    """Return the most recent financial snapshot for this symbol."""
    conn = _get_financials_conn()
    try:
        row = conn.execute("""
            SELECT metric_date, pe_ratio, eps, dividend_yield, roe,
                   market_cap, revenue, current_price, target_price, fetched_at
            FROM financial_metrics
            WHERE symbol = ?
            ORDER BY metric_date DESC
            LIMIT 1;
        """, [symbol.upper()]).fetchone()
        if not row:
            return None
        keys = ["metric_date","pe_ratio","eps","dividend_yield","roe",
                "market_cap","revenue","current_price","target_price","fetched_at"]
        return dict(zip(keys, row))
    finally:
        conn.close()


def is_financial_stale(symbol: str, max_age_days: int = 7) -> bool:
    """Return True if financial data is older than max_age_days."""
    latest = get_latest_financial(symbol)
    if latest is None:
        return True
    return (date.today() - latest["metric_date"]) > timedelta(days=max_age_days)
