"""
backfill_historical.py — 後台歷史數據下載器
============================================
並發下載 S&P 500 / NASDAQ-100 / HSI 所有成分股的歷史K線，
存入 DuckDB，出現網絡錯誤自動重試，完成後 checkpoint 記錄。

用法：
  # 前台演示（1年，並發8）：
  python backfill_historical.py --days 365 --workers 8

  # 後台運行（10年，並發8）：
  nohup python backfill_historical.py --days 3650 --workers 8 >> backfill.log 2>&1 &

  # 恢復中斷的下載（自動讀 checkpoint）：
  python backfill_historical.py --days 3650 --workers 8 --resume

  # 只下載某個指數：
  python backfill_historical.py --index sp500 --days 365 --workers 8
"""

import os
import sys
import time
import json
import signal
import argparse
import warnings
from datetime import datetime, date
from pathlib import Path
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from typing import Optional, Tuple, List

import requests
import pandas as pd
import numpy as np
import duckdb

# ── Yahoo v8 API 下載 ─────────────────────────────────────────────
YAHOO_V8_BASE = "https://query1.finance.yahoo.com/v8/finance/chart"
_HEADERS = {"User-Agent": "Mozilla/5.0"}

def _yahoo_v8(ticker: str, days: int, retry: int = 3) -> Optional[dict]:
    """直接調用 Yahoo Finance v8 REST API，繞過 yfinance 封裝層限制"""
    for attempt in range(retry):
        try:
            params = {"interval": "1d", "range": f"{days}d"}
            r = requests.get(
                f"{YAHOO_V8_BASE}/{ticker}",
                params=params,
                headers=_HEADERS,
                timeout=15,
            )
            if r.status_code != 200:
                time.sleep(2 ** attempt)
                continue
            data = r.json()
            result = data.get("chart", {}).get("result")
            if not result:
                time.sleep(2 ** attempt)
                continue
            return result[0]
        except Exception:
            if attempt < retry - 1:
                time.sleep(2 ** attempt)
            else:
                return None
    return None

def download_ticker(ticker: str, days: int, retry: int = 3) -> Optional[pd.DataFrame]:
    """下載單檔歷史數據，失敗時自動重試"""
    raw = _yahoo_v8(ticker, days, retry)
    if not raw:
        return None

    timestamps = raw.get("timestamp", [])
    quote = raw.get("indicators", {}).get("quote", [{}])[0]
    adj = raw.get("indicators", {}).get("adjclose", [])
    raw_adj = adj[0].get("adjclose") if adj and isinstance(adj[0], dict) else None
    adjclose = raw_adj if raw_adj else quote.get("close")

    if not timestamps or not adjclose or len(adjclose) < len(timestamps) * 0.8:
        return None

    close    = [c for c in (quote.get("close") or [])]
    opens    = [o for o in (quote.get("open")  or [])]
    highs    = [h for h in (quote.get("high")  or [])]
    lows     = [l for l in (quote.get("low")   or [])]
    volumes  = [v for v in (quote.get("volume") or [])]
    adj_list = [a for a in adjclose]

    n = min(len(timestamps), len(adj_list))
    rows = []
    for i in range(n):
        rows.append({
            "trade_date": pd.Timestamp(timestamps[i], unit="s").normalize().date(),
            "symbol":     ticker,
            "open":       opens[i]    if i < len(opens)    else adj_list[i],
            "high":       highs[i]    if i < len(highs)     else adj_list[i],
            "low":        lows[i]     if i < len(lows)      else adj_list[i],
            "close":      adj_list[i],
            "volume":     int(volumes[i]) if i < len(volumes) and volumes[i] else 0,
            "currency":   "USD",
        })

    df = pd.DataFrame(rows)
    return df

# ── 路徑設定 ──────────────────────────────────────────────────────
BASE_DIR  = Path("/Users/aiagent/projects/dashboard")
DATA_DIR  = BASE_DIR / "data"
DB_PATH   = DATA_DIR / "prices.ddb"
CKPT_FILE = DATA_DIR / "backfill_checkpoint.json"
LOG_DIR   = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

# ── 參數解析 ──────────────────────────────────────────────────────
parser = argparse.ArgumentParser(description="後台歷史數據下載器")
parser.add_argument("--days",    type=int, default=365,  help="下載多少天歷史（默認365）")
parser.add_argument("--workers", type=int, default=8,   help="並發下載綫程數（默認8）")
parser.add_argument("--index",   type=str, default="all", help="只下載某指數：sp500 / nasdaq100 / hsi / all")
parser.add_argument("--resume",  action="store_true",    help="從 checkpoint 恢復中斷的下載")
parser.add_argument("--batch-size", type=int, default=30, help="每批次休息幾秒（防Yahoo限速）")
parser.add_argument("--log-file", type=str, default=None, help="日誌文件路徑")
args = parser.parse_args()

LOG_FILE = args.log_file or str(LOG_DIR / f"backfill_{date.today().isoformat()}.log")

# ── 日誌 ───────────────────────────────────────────────────────────
def log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] [{level}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")

# ── 載入成分股列表 ─────────────────────────────────────────────────
def load_tickers(index_name: str) -> list[str]:
    def read_file(p: Path) -> list[str]:
        if not p.exists():
            return []
        return [t.strip() for t in p.read_text().splitlines() if t.strip()]

    if index_name == "all":
        return (
            read_file(DATA_DIR / "constituents_sp500.txt") +
            read_file(DATA_DIR / "constituents_nasdaq100.txt") +
            read_file(DATA_DIR / "constituents_hsi.txt")
        )
    return read_file(DATA_DIR / f"constituents_{index_name}.txt")

# ── DuckDB 工具 ────────────────────────────────────────────────────
def get_ddb_conn() -> duckdb.DuckDBPyConnection:
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

def get_db_tickers(conn) -> set:
    """返回數據庫中已有數據的 ticker 集合（用於增量跳過）"""
    try:
        result = conn.execute(
            "SELECT DISTINCT symbol FROM stock_prices"
        ).fetchall()
        return {r[0] for r in result}
    except Exception:
        return set()

# ── DuckDB 工具 ────────────────────────────────────────────────────
def bulk_upsert(conn, rows: List[dict]):
    """批量 upsert 到 DuckDB"""
    if not rows:
        return
    fetched_at = datetime.now()
    cols = ["trade_date", "symbol", "open", "high", "low", "close", "volume", "currency"]
    values = []
    for r in rows:
        row = []
        for c in cols:
            v = r[c]
            if c != "symbol" and c != "trade_date" and c != "currency" and v != v:  # NaN check
                v = None
            row.append(v)
        values.append(tuple(row) + (fetched_at,))
    conn.executemany(
        """INSERT INTO stock_prices (trade_date, symbol, open, high, low, close, volume, currency, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (trade_date, symbol) DO UPDATE SET
               open       = excluded.open,
               high       = excluded.high,
               low        = excluded.low,
               close      = excluded.close,
               volume     = excluded.volume,
               fetched_at = excluded.fetched_at""",
        values,
    )

# ── 主下載邏輯 ─────────────────────────────────────────────────────
def download_batch(tickers: list[str], days: int, conn,
                   progress_lock: Lock,
                   stats: dict,
                   batch_size: int = 30) -> list[str]:
    """
    並發下載一批 tickers，返回下載失敗的 ticker 列表
    """
    failed = []
    now = datetime.now()

    def worker(ticker: str) -> Tuple[str, Optional[pd.DataFrame], Optional[str]]:
        df = download_ticker(ticker, days)
        if df is None or df.empty:
            return (ticker, None, f"{ticker}: download returned empty")
        return (ticker, df, None)

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(worker, t): t for t in tickers}

        for future in as_completed(futures):
            ticker, df, err = future.result()
            if err:
                failed.append(ticker)
                with progress_lock:
                    stats["errors"].append(err)
            else:
                rows = df.to_dict("records")
                # 確保 fetched_at 被設定（不能依賴 DB DEFAULT）
                for r in rows:
                    r["fetched_at"] = now
                bulk_upsert(conn, rows)
                with progress_lock:
                    stats["saved"] += len(rows)
                    stats["ok"].append(ticker)

            with progress_lock:
                stats["done"] += 1
                done = stats["done"]
                total = stats["total"]
                saved = stats["saved"]
                log(f"  [{done}/{total}] {'✅' if err is None else '❌'} {ticker}  (累計 {saved} rows)", "PROGRESS" if err is None else "WARNING")

    return failed

# ── Checkpoint 管理 ────────────────────────────────────────────────
def load_checkpoint() -> dict:
    if not args.resume or not CKPT_FILE.exists():
        return {"done": [], "failed": [], "last_updated": None}
    try:
        return json.loads(CKPT_FILE.read_text())
    except Exception:
        return {"done": [], "failed": [], "last_updated": None}

def save_checkpoint(ckpt: dict):
    ckpt["last_updated"] = datetime.now().isoformat()
    CKPT_FILE.write_text(json.dumps(ckpt, indent=2))

# ── 訊號處理（優雅退出）─────────────────────────────────────────────
_running = True
def _signal_handler(sig, frame):
    global _running
    _running = False
    log("收到退出信號，正在保存 checkpoint...", "WARN")
    sys.exit(0)

signal.signal(signal.SIGINT,  _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)

# ── 主流程 ─────────────────────────────────────────────────────────
def main():
    log("=" * 60)
    log(f"backfill_historical.py  啟動")
    log(f"  天數:    {args.days}")
    log(f"  並發數:  {args.workers}")
    log(f"  指數:    {args.index}")
    log(f"  恢復模式: {args.resume}")
    log(f"  日誌:    {LOG_FILE}")
    log("=" * 60)

    all_tickers = load_tickers(args.index)
    log(f"待下載股票: {len(all_tickers)} 檔")

    conn = get_ddb_conn()
    db_tickers = get_db_tickers(conn)
    log(f"數據庫已有: {len(db_tickers)} 檔")

    # ── 過濾已下載的股票（增量保護）────────────────────────────
    ckpt = load_checkpoint()
    already_done = set(ckpt["done"]) | db_tickers

    tickers_to_download = [t for t in all_tickers if t not in already_done]
    log(f"需要下載: {len(tickers_to_download)} 檔  (跳過已存在: {len(already_done)})")

    if not tickers_to_download:
        log("所有股票均已下載完成，無需操作", "INFO")
        conn.close()
        return

    # ── 統計狀態 ────────────────────────────────────────────────
    stats = {"done": 0, "total": len(tickers_to_download), "saved": 0, "ok": [], "errors": []}
    progress_lock = Lock()
    failed: list[str] = []

    # 為避免觸發 Yahoo 頻寬限制，每次最多連續處理 batch_size 檔後短暫休息
    BATCH = args.batch_size
    segments = [
        tickers_to_download[i:i+BATCH]
        for i in range(0, len(tickers_to_download), BATCH)
    ]

    total_start = time.time()
    for seg_idx, segment in enumerate(segments):
        if not _running:
            break
        seg_start = time.time()
        log(f"\n批次 {seg_idx+1}/{len(segments)}  ({len(segment)} 檔)...")
        seg_failed = download_batch(segment, args.days, conn, progress_lock, stats)
        failed.extend(seg_failed)

        # checkpoint 每批次存一次
        ckpt["done"] = stats["ok"]
        ckpt["failed"] = failed
        save_checkpoint(ckpt)

        seg_elapsed = time.time() - seg_start
        ok_count = len(segment) - len(seg_failed)
        log(f"  批次完成: {ok_count}/{len(segment)} ✅  耗时: {seg_elapsed:.1f}s  累計: {stats['saved']} rows")

        # 批次間休息 3 秒，降低被限速風險
        if seg_idx < len(segments) - 1:
            time.sleep(3)

    # ── 結果摘要 ──────────────────────────────────────────────────
    total_elapsed = time.time() - total_start
    success = len(stats["ok"])
    failed_count = len(failed)

    log("\n" + "=" * 60)
    log("下載完成摘要")
    log(f"  成功:   {success} 檔")
    log(f"  失敗:   {failed_count} 檔  {failed[:20]}{'...' if len(failed)>20 else ''}")
    log(f"  總耗時: {total_elapsed/60:.1f} 分鐘")
    log(f"  總行數: {stats['saved']} rows")
    log("=" * 60)

    # 最終 checkpoint
    ckpt["done"] = stats["ok"]
    ckpt["failed"] = failed
    save_checkpoint(ckpt)
    log(f"Checkpoint 已保存: {CKPT_FILE}")

    conn.close()

if __name__ == "__main__":
    main()
