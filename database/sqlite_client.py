"""
SQLite client for search history and news articles.
"""

import sqlite3
import json
import os
from datetime import datetime, date
from typing import Optional, List, Dict, Any

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
DASHBOARD_DB = os.path.join(DATA_DIR, "dashboard.db")

os.makedirs(DATA_DIR, exist_ok=True)


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DASHBOARD_DB, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")   # Better concurrent read/write
    conn.execute("PRAGMA synchronous=NORMAL;") # Faster writes, safe with WAL
    return conn


def init_tables() -> None:
    conn = _get_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS search_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                query       TEXT NOT NULL,
                symbol      TEXT NOT NULL,
                market      TEXT,
                searched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_search_symbol
            ON search_history(symbol);
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_search_time
            ON search_history(searched_at DESC);
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS news_articles (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol      TEXT NOT NULL,
                title       TEXT,
                link        TEXT UNIQUE,
                pub_date    TIMESTAMP,
                source      TEXT,
                content     TEXT,
                sentiment   REAL,
                fetched_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_news_symbol
            ON news_articles(symbol);
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_news_pubdate
            ON news_articles(pub_date DESC);
        """)
        conn.commit()
    finally:
        conn.close()


# ─── Search History ────────────────────────────────────────────────────────────

def add_search(query: str, symbol: str, market: Optional[str] = None) -> int:
    """Record a stock search. Returns the new row id."""
    conn = _get_conn()
    try:
        cur = conn.execute("""
            INSERT INTO search_history (query, symbol, market)
            VALUES (?, ?, ?);
        """, [query.strip(), symbol.upper(), market])
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_recent_searches(limit: int = 20) -> List[Dict[str, Any]]:
    """Return the most recent searches."""
    conn = _get_conn()
    try:
        rows = conn.execute("""
            SELECT id, query, symbol, market, searched_at
            FROM search_history
            ORDER BY searched_at DESC
            LIMIT ?;
        """, [limit]).fetchall()
        cols = ["id", "query", "symbol", "market", "searched_at"]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        conn.close()


def get_search_count_by_symbol() -> List[Dict[str, Any]]:
    """Return search frequency per symbol (for "most searched" insights)."""
    conn = _get_conn()
    try:
        rows = conn.execute("""
            SELECT symbol, market, COUNT(*) as cnt
            FROM search_history
            GROUP BY symbol
            ORDER BY cnt DESC
            LIMIT 20;
        """).fetchall()
        return [dict(zip(["symbol","market","count"], r)) for r in rows]
    finally:
        conn.close()


# ─── News Articles ─────────────────────────────────────────────────────────────

def upsert_news(symbol: str, title: str, link: str,
                pub_date: Optional[datetime] = None,
                source: Optional[str] = None,
                content: Optional[str] = None,
                sentiment: Optional[float] = None) -> bool:
    """
    Insert a news article, skipping duplicates (link is UNIQUE).
    Returns True if inserted, False if skipped.
    """
    conn = _get_conn()
    try:
        cur = conn.execute("""
            INSERT OR IGNORE INTO news_articles
                (symbol, title, link, pub_date, source, content, sentiment)
            VALUES (?, ?, ?, ?, ?, ?, ?);
        """, [symbol.upper(), title, link,
              pub_date.isoformat() if pub_date else None,
              source, content, sentiment])
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def get_news(symbol: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Return recent news for a symbol."""
    conn = _get_conn()
    try:
        rows = conn.execute("""
            SELECT id, symbol, title, link, pub_date, source, content, sentiment, fetched_at
            FROM news_articles
            WHERE symbol = ?
            ORDER BY pub_date DESC
            LIMIT ?;
        """, [symbol.upper(), limit]).fetchall()
        cols = ["id","symbol","title","link","pub_date","source","content","sentiment","fetched_at"]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        conn.close()


def get_latest_news_fetch(symbol: str) -> Optional[datetime]:
    """Return when we last fetched news for this symbol."""
    conn = _get_conn()
    try:
        row = conn.execute("""
            SELECT MAX(fetched_at) FROM news_articles WHERE symbol = ?;
        """, [symbol.upper()]).fetchone()
        if row and row[0]:
            return datetime.fromisoformat(row[0])
        return None
    finally:
        conn.close()


def is_news_stale(symbol: str, max_age_minutes: int = 30) -> bool:
    """Return True if news for this symbol is older than max_age_minutes."""
    latest = get_latest_news_fetch(symbol)
    if latest is None:
        return True
    delta = datetime.now() - latest
    return delta.total_seconds() > (max_age_minutes * 60)


def get_all_news_symbols() -> List[str]:
    """Return all symbols we have cached news for."""
    conn = _get_conn()
    try:
        rows = conn.execute("""
            SELECT DISTINCT symbol FROM news_articles ORDER BY symbol;
        """).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


# ─── Table Init on Import ───────────────────────────────────────────────────────

init_tables()
