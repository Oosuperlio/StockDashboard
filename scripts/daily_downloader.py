"""
scripts/daily_downloader.py — 每日增量更新 cron 腳本
====================================================
每天自動下載最新交易日數據（只下載近3天），增量寫入 DuckDB。
需要 backfill_historical.py 先完成歷史數據填充。

用法（crontab -e）：
  0 9 * * * cd /Users/aiagent/projects/dashboard && python3 scripts/daily_downloader.py >> logs/daily_downloader.log 2>&1

依賴：yfinance, pandas, duckdb（已在 requirements.txt）
"""

import os
import sys
import time
import signal
import logging
from datetime import datetime, date, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from typing import Optional

BASE_DIR = Path("/Users/aiagent/projects/dashboard")
sys.path.insert(0, str(BASE_DIR))

import yfinance as yf
import requests
import pandas as pd
import duckdb

# ── Yahoo Finance v8 REST API ────────────────────────────────────
def _yahoo_v8(ticker: str, days: int = 7) -> Optional[pd.DataFrame]:
    """Yahoo Finance v8 REST API — bypasses yfinance封装层"""
    # 轉換含 . 的 ticker（如 BRK.B → BRK-B）避免 API 500 錯誤
    api_ticker = ticker.replace(".", "-")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{api_ticker}"
    params = {"interval": "1d", "range": f"{days}d"}
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        data = resp.json()
        result = data.get("chart", {}).get("result", [])
        if not result:
            return None
        timestamps = result[0]["timestamp"]
        ohlcv = result[0]["indicators"]["quote"][0]
        df = pd.DataFrame(ohlcv, index=pd.to_datetime(timestamps, unit="s"))
        df.index = df.index.tz_localize(None)
        df = df[["open", "high", "low", "close", "volume"]]
        return df
    except Exception:
        return None


# ── 日誌設定 ──────────────────────────────────────────────────────
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_DIR / "daily_downloader.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger()

# ── DB 設定 ───────────────────────────────────────────────────────
DB_PATH = BASE_DIR / "data" / "prices.ddb"

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

def get_last_date(conn, ticker: str) -> Optional[date]:
    """返回該股票在 DB 中最新的 trade_date"""
    try:
        row = conn.execute(
            "SELECT MAX(trade_date) FROM stock_prices WHERE symbol = ?", [ticker]
        ).fetchone()
        if row and row[0]:
            return date.fromisoformat(str(row[0]))
    except Exception:
        pass
    return None

def upsert_rows(conn, rows: list[dict]):
    if not rows:
        return
    fetched_at = datetime.now()
    cols = ["trade_date", "symbol", "open", "high", "low", "close", "volume", "currency"]
    values = []
    for r in rows:
        row = []
        for c in cols:
            v = r[c]
            # NaN check: NaN != NaN is True
            if c not in ("symbol", "trade_date", "currency") and isinstance(v, float) and v != v:
                v = None
            row.append(v)
        values.append(tuple(row) + (fetched_at,))
    conn.executemany(
        """INSERT INTO stock_prices (trade_date, symbol, open, high, low, close, volume, currency, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (trade_date, symbol) DO UPDATE SET
               open = excluded.open, high = excluded.high, low = excluded.low,
               close = excluded.close, volume = excluded.volume, fetched_at = excluded.fetched_at""",
        values,
    )

# ── 載入全量成分股 ───────────────────────────────────────────────
def all_tickers() -> list[str]:
    files = [
        BASE_DIR / "data" / "constituents_sp500.txt",
        BASE_DIR / "data" / "constituents_nasdaq100.txt",
        BASE_DIR / "data" / "constituents_hsi.txt",
    ]
    tickers = []
    for p in files:
        if p.exists():
            tickers.extend(t.strip() for t in p.read_text().splitlines() if t.strip())
    return sorted(set(tickers))

# ── 下載函式 ──────────────────────────────────────────────────────
def fetch_latest(ticker: str, lookback: int = 7) -> Optional[pd.DataFrame]:
    """
    下載近 lookback 天的數據，自動過濾重複。
    如果 DB 已有最新日期，則返回空 DataFrame（無需寫入）。
    先試 Yahoo v8 API，再試 yfinance fallback。
    """
    # Try Yahoo v8 API first
    df = _yahoo_v8(ticker, lookback)
    if df is not None and not df.empty and df["close"].gt(0).any():
        df = df[df["close"] > 0].copy()
        df = df.reset_index()
        df.columns = ["date", "open", "high", "low", "close", "volume"]
        df["trade_date"] = pd.to_datetime(df["date"]).dt.date
        df["symbol"] = ticker
        df["currency"] = "USD"
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["volume"] = df["volume"].fillna(0).astype("int64")
        return df[["trade_date", "symbol", "open", "high", "low", "close", "volume", "currency"]]

    # yfinance fallback
    try:
        ticker_obj = yf.Ticker(ticker)
        hist = ticker_obj.history(period=f"{lookback}d", auto_adjust=True)
        if hist.empty:
            return None
        hist = hist.reset_index()
        hist.columns = [c.lower() if isinstance(c, str) else c for c in hist.columns]
        date_col = [c for c in hist.columns if "date" in c.lower()][0]
        hist["trade_date"] = pd.to_datetime(hist[date_col]).dt.date
        hist["symbol"] = ticker
        hist["currency"] = getattr(ticker_obj, "info", {}).get("currency", "USD")
        for col in ["open", "high", "low", "close", "volume"]:
            if col in hist.columns:
                hist[col] = pd.to_numeric(hist[col], errors="coerce")
        hist["volume"] = hist["volume"].fillna(0).astype("int64")
        return hist[["trade_date", "symbol", "open", "high", "low", "close", "volume", "currency"]]
    except Exception as e:
        log.warning(f"[{ticker}] fetch error: {e}")
        return None


# ── 每日下載主邏輯 ────────────────────────────────────────────────
WORKERS = 8  # 並發數

def daily_update():
    log.info("=" * 50)
    log.info(f"每日增量下載  開始  ({date.today()})")

    tickers = all_tickers()
    log.info(f"待更新股票: {len(tickers)} 檔")

    stats = {"updated": 0, "skipped": 0, "errors": 0}
    lock = Lock()

    def worker(ticker: str):
        nonlocal stats
        # 每個 worker 執行緒自有 DB 連線（避免執行緒安全問題）
        conn = get_conn()
        try:
            # 先看 DB 最新日期
            last_date = get_last_date(conn, ticker)
            df = fetch_latest(ticker, lookback=7)
            if df is None or df.empty:
                with lock:
                    stats["errors"] += 1
                return

            # 過濾：只保留 last_date 之後的新數據（精準增量）
            if last_date:
                df = df[df["trade_date"] > last_date]

            if df.empty:
                with lock:
                    stats["skipped"] += 1
                return

            upsert_rows(conn, df.to_dict("records"))
            with lock:
                stats["updated"] += 1
            log.info(f"  [{ticker}] +{len(df)} rows (DB last: {last_date})")
        finally:
            conn.close()

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = {executor.submit(worker, t): t for t in tickers}
        for i, future in enumerate(as_completed(futures), 1):
            future.result()

    elapsed = time.time() - t0

    log.info("─" * 50)
    log.info(f"完成  ({elapsed:.1f}s)")
    log.info(f"  更新: {stats['updated']} 檔")
    log.info(f"  跳過: {stats['skipped']} 檔（已是最新）")
    log.info(f"  失敗: {stats['errors']} 檔")

    # 簡單健康檢查
    if stats["errors"] > len(tickers) * 0.1:
        log.warning(f"錯誤率 {stats['errors']/len(tickers)*100:.0f}% > 10%，請檢查網絡")

    return stats

# ── 入口 ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    # 優雅退出
    signal.signal(signal.SIGINT,  lambda s, f: (log.info("中斷"), sys.exit(0)))
    signal.signal(signal.SIGTERM, lambda s, f: (log.info("終止"), sys.exit(0)))
    daily_update()
