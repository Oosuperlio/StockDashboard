"""
Cache layer: read from local DB first, fetch from API only when stale.
Provides a unified interface for app.py to get stock data.
"""

import yfinance as yf
import pandas as pd
import feedparser
import re
from datetime import date, datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple

from . import duckdb_client as db
from . import sqlite_client as sq

# ─── Config ────────────────────────────────────────────────────────────────────

MAX_NEWS_AGE_MINUTES     = 30   # refresh news if older than this
MAX_PRICE_AGE_DAYS       = 1    # refresh price if older than this
MAX_FINANCIAL_AGE_DAYS   = 7    # refresh financials if older than this

YAHOO_SEARCH_URL = "https://query2.finance.yahoo.com/v1/finance/search?q={q}"
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"

NEWS_QUERY_MAP = {
    "3968.HK": "China Merchants Bank",
    "0700.HK": "Tencent Holdings",
    "0005.HK": "HSBC Holdings Hong Kong",
    "9988.HK": "Alibaba Group",
    "3690.HK": "Meituan",
    "9618.HK": "JD.com",
    "1024.HK": "Xiaomi Corporation",
}


# ─── Stock Symbol Suggestions ──────────────────────────────────────────────────

def suggest_symbols(query: str) -> List[Dict[str, str]]:
    """
    Return a list of symbol suggestions from Yahoo Finance.
    Used for autocomplete in the search box.
    """
    import requests
    url = YAHOO_SEARCH_URL.format(q=query)
    try:
        r = requests.get(url, timeout=5,
                         headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        data = r.json()
        results = []
        for item in data.get("quotes", [])[:8]:
            results.append({
                "symbol":  item.get("symbol", ""),
                "name":    item.get("shortName") or item.get("longName", ""),
                "exch":    item.get("exchange", ""),
                "type":    item.get("quoteType", ""),
            })
        return results
    except Exception:
        return []


# ─── News ───────────────────────────────────────────────────────────────────────

def _parse_google_news(symbol: str, queryOverride: Optional[str] = None) -> List[Dict]:
    """Fetch and parse Google News RSS. Returns list of parsed articles."""
    query = NEWS_QUERY_MAP.get(symbol, symbol)
    url = GOOGLE_NEWS_RSS.format(q=query.replace(" ", "+"))
    feed = feedparser.parse(url)
    articles = []
    for entry in feed.entries[:30]:
        title = ""
        if hasattr(entry, "title"):
            title = entry.title
            # Strip <![CDATA[ ... ]]> or HTML tags
            title = re.sub(r"<!\[CDATA\[|\]\]>", "", title)
            title = re.sub(r"<[^>]+>", "", title).strip()

        pub_date = None
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            from time import mktime
            pub_date = datetime.fromtimestamp(mktime(entry.published_parsed))

        source = getattr(entry, "source", None)
        if source and hasattr(source, "title"):
            source = source.title

        articles.append({
            "title":    title,
            "link":     getattr(entry, "link", ""),
            "pub_date": pub_date,
            "source":   source or "Google News",
        })
    return articles


def get_news(symbol: str, force_refresh: bool = False) -> List[Dict]:
    """
    Get news for symbol: from local DB if fresh, otherwise fetch + cache.
    Returns list of article dicts.
    """
    symbol = symbol.upper()
    if not force_refresh and not sq.is_news_stale(symbol, MAX_NEWS_AGE_MINUTES):
        # Return cached news immediately (no API call)
        return sq.get_news(symbol)

    # Fetch fresh news
    articles = _parse_google_news(symbol)

    # Deduplicate and save to DB
    new_count = 0
    for art in articles:
        inserted = sq.upsert_news(
            symbol     = symbol,
            title      = art["title"],
            link       = art["link"],
            pub_date   = art["pub_date"],
            source     = art["source"],
            sentiment  = None,
        )
        if inserted:
            new_count += 1

    return sq.get_news(symbol)


def _gap_fill_from_yfinance(symbol: str, hist: pd.DataFrame, currency: str) -> int:
    """
    Compare hist (yfinance) dates against current DuckDB cache.
    Insert any dates present in yfinance but missing from DuckDB.
    Returns number of gaps filled.
    """
    cached_dates = {row["trade_date"] for row in db.get_price_range(symbol, 999)}
    # hist index is Timestamp; normalize to date objects
    yfinance_dates = {
        (idx.date() if hasattr(idx, "date") else idx) for idx in hist.index
    }
    missing = yfinance_dates - cached_dates
    filled = 0
    for idx, row in hist.iterrows():
        trade_date = idx.date() if hasattr(idx, "date") else idx
        if isinstance(trade_date, str):
            from datetime import datetime as dt
            trade_date = dt.fromisoformat(trade_date).date()
        if trade_date in missing:
            if not (pd.isna(row["Open"]) or pd.isna(row["High"]) or
                    pd.isna(row["Low"])  or pd.isna(row["Close"])):
                db.upsert_price(
                    symbol, trade_date,
                    float(row["Open"]), float(row["High"]),
                    float(row["Low"]),  float(row["Close"]),
                    int(row["Volume"]), currency,
                )
                filled += 1
    return filled


# ─── Price History ─────────────────────────────────────────────────────────────

def get_price_history(symbol: str, days: int = 90,
                      force_refresh: bool = False) -> List[Dict]:
    """
    Get price history: from DuckDB if fresh, otherwise fetch + cache.
    Returns list of OHLCV dicts, newest-first.

    Data integrity: always uses yfinance as authoritative source.
    On force_refresh, wipes stale cache and rebuilds from yfinance to
    eliminate any gaps introduced by previous Tencent-sourced caches.
    """
    symbol = symbol.upper()
    stale  = db.is_price_stale(symbol, MAX_PRICE_AGE_DAYS)

    # ── Force refresh: nuke stale cache, rebuild from yfinance ──────────────
    if force_refresh and stale:
        db.delete_price_range(symbol, days)

    if not force_refresh and not stale:
        cached = db.get_price_range(symbol, days)
        # Cross-source sanity check: if cached rows < 80% of expected
        # trading days, force a rebuild from yfinance directly.
        expected_min = int(days * 0.80)
        if len(cached) >= expected_min:
            # Also verify the cached data actually covers the requested date range.
            # DuckDB query uses trade_date >= CURRENT_DATE - days, but if the cache
            # was populated with a shorter lookback (e.g. 90d) the rows will pass
            # the count check but miss the earliest dates needed for 365d.
            from datetime import date, timedelta
            min_required = date.today() - timedelta(days=days)
            # cached is ordered newest-first; last element = oldest date
            oldest_cached = cached[-1]["trade_date"] if cached else None
            if oldest_cached and oldest_cached <= min_required:
                return cached
        # Cache miss: either too few rows OR date range too narrow → wipe and refetch
        db.delete_price_range(symbol, days)

    # ── Fetch from yfinance (authoritative source) ─────────────────────────
    ticker = yf.Ticker(symbol)
    hist = ticker.history(period=f"{days}d", auto_adjust=True)
    if hist.empty:
        cached = db.get_price_range(symbol, days)
        return cached if cached else []

    currency = "HKD" if symbol.endswith(".HK") else "USD"

    written = 0
    for idx, row in hist.iterrows():
        if (pd.isna(row["Open"]) or pd.isna(row["High"]) or
            pd.isna(row["Low"])  or pd.isna(row["Close"])):
            continue
        trade_date = idx.date() if hasattr(idx, "date") else idx
        if isinstance(trade_date, str):
            from datetime import datetime as dt
            trade_date = dt.fromisoformat(trade_date).date()
        db.upsert_price(
            symbol     = symbol,
            trade_date = trade_date,
            open_      = float(row["Open"]),
            high       = float(row["High"]),
            low        = float(row["Low"]),
            close      = float(row["Close"]),
            volume     = int(row["Volume"]),
            currency   = currency,
        )
        written += 1

    # ── Gap-fill: detect missing dates and backfill from yfinance ─────────
    filled = _gap_fill_from_yfinance(symbol, hist, currency)
    if filled:
        pass  # gap-fill results are already in DuckDB

    result = db.get_price_range(symbol, days)
    return result


# ─── Financial Metrics ─────────────────────────────────────────────────────────

def get_financial_metrics(symbol: str,
                          force_refresh: bool = False) -> Optional[Dict]:
    """
    Get financial metrics snapshot: from DuckDB if fresh, otherwise fetch + cache.
    Returns a dict or None.
    """
    symbol = symbol.upper()
    if not force_refresh and not db.is_financial_stale(symbol, MAX_FINANCIAL_AGE_DAYS):
        return db.get_latest_financial(symbol)

    # Fetch from yfinance
    try:
        info = yf.Ticker(symbol).info
    except Exception:
        cached = db.get_latest_financial(symbol)
        return cached if cached else {}

    metrics = {
        "pe_ratio":        info.get("trailingPE"),
        "eps":             info.get("trailingEps") or info.get("forwardEps"),
        "dividend_yield":  info.get("dividendYield"),
        "roe":             info.get("returnOnEquity"),
        "market_cap":      info.get("marketCap"),
        "revenue":         info.get("totalRevenue") or info.get("revenue"),
        "current_price":   info.get("currentPrice") or info.get("regularMarketPrice"),
        "target_price":    info.get("targetMeanPrice"),
    }

    db.upsert_financial(symbol, date.today(), metrics)
    return db.get_latest_financial(symbol)


# ─── Stock Info (full) ─────────────────────────────────────────────────────────

def get_stock_info(symbol: str) -> Dict[str, Any]:
    """
    Return live stock.info from Yahoo Finance.
    Cached only in-memory for the session; does not write to disk.
    """
    try:
        return yf.Ticker(symbol.upper()).info or {}
    except Exception:
        return {}


# ─── Search History ─────────────────────────────────────────────────────────────

def record_search(query: str, symbol: str, market: Optional[str] = None) -> int:
    """Log a search to SQLite."""
    return sq.add_search(query, symbol, market)


def get_recent_searches(limit: int = 20) -> List[Dict]:
    """Return recent search history."""
    return sq.get_recent_searches(limit)


def get_top_searched(limit: int = 10) -> List[Dict]:
    """Return most-searched symbols."""
    return sq.get_search_count_by_symbol()[:limit]


# ─── All-in-one loader ─────────────────────────────────────────────────────────

def load_stock_data(symbol: str,
                    force_refresh: bool = False) -> Dict[str, Any]:
    """
    Load all cached data for a symbol: prices, financials, news.
    API called only when local cache is stale.
    Returns a unified dict for app.py to render.
    """
    symbol = symbol.upper()
    return {
        "symbol":     symbol,
        "prices":     get_price_history(symbol, force_refresh=force_refresh),
        "financials": get_financial_metrics(symbol, force_refresh=force_refresh),
        "news":       get_news(symbol, force_refresh=force_refresh),
    }
