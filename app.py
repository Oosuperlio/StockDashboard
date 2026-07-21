"""
Stock Dashboard
實時股票走势 Dashboard — 使用 Yahoo Finance API

功能：
1. 股票代碼搜尋 + 自動建議（支援港股/美股）
2. 實時股價 + 歷史走勢圖
3. 最新消息（Google News RSS）
4. 財務指標（估值 / 盈利能力 / 增長 / 分析師觀點）
"""

import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
import requests
from datetime import datetime

# Holiday calendars
try:
    import holidays
    HKEX_HOLIDAYS = holidays.HongKong()
    US_HOLIDAYS    = holidays.US()
except Exception:
    import warnings
    warnings.warn("holidays library not available, using empty holiday set")
    HKEX_HOLIDAYS = set()
    US_HOLIDAYS    = set()

def get_market_from_ticker(ticker: str) -> str:
    """Detect market from ticker suffix: HK (.HK), US (default)."""
    t = ticker.upper()
    if t.endswith(".HK"):
        return "HK"
    return "US"


def is_market_trading_day(ticker: str, date: pd.Timestamp) -> bool:
    """Return True if date is a valid trading day (not weekend, not holiday)."""
    market = get_market_from_ticker(ticker)
    holidays = HKEX_HOLIDAYS if market == "HK" else US_HOLIDAYS
    # Normalize to date without timezone for holiday lookup
    d = date.normalize().date() if hasattr(date, 'date') else date.date()
    # Weekend check
    if d.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    # Holiday check
    if holidays and d in holidays:
        return False
    return True


def filter_trading_days(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """Drop rows that are not trading days (weekends/holidays) BEFORE pattern detection.
    This ensures pattern indices and chart indices stay in sync."""
    mask = df.index.map(lambda d: is_market_trading_day(ticker, d))
    return df[mask]

st.set_page_config(page_title="Stock Dashboard", page_icon="📈", layout="wide")

# ── Handle signal card click navigation (from ?nav_ticker=SYMBOL) ──
nav_ticker = st.query_params.get("nav_ticker")
if nav_ticker:
    st.session_state["navigate_to_ticker"] = nav_ticker
    st.query_params.clear()
    st.rerun()

# ── Custom card CSS ──────────────────────────────────────────────────────────
st.markdown("""
<style>
.card {
    background: #1e2533;
    border: 1px solid #2d3748;
    border-radius: 12px;
    padding: 16px 20px;
    margin-bottom: 12px;
}
.card-header {
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: #718096;
    margin-bottom: 8px;
}
.card-value {
    font-size: 26px;
    font-weight: 700;
    color: #e2e8f0;
    line-height: 1;
}
.card-delta {
    font-size: 13px;
    font-weight: 500;
    margin-top: 4px;
}
.up { color: #48bb78; }
.down { color: #fc8181; }
.neutral { color: #a0aec0; }
.section-card {
    background: #1a2035;
    border: 1px solid #252e45;
    border-radius: 14px;
    padding: 18px 20px;
    margin-bottom: 14px;
}
.section-title {
    font-size: 13px;
    font-weight: 700;
    color: #cbd5e0;
    margin-bottom: 14px;
    border-bottom: 1px solid #2d3748;
    padding-bottom: 8px;
}
.metric-pair { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
.metric-label { font-size: 12px; color: #718096; }
.metric-val { font-size: 14px; font-weight: 600; color: #e2e8f0; }
.news-card {
    background: #1a2035;
    border: 1px solid #252e45;
    border-radius: 10px;
    padding: 12px 14px;
    margin-bottom: 10px;
}
.news-title { font-size: 13px; font-weight: 600; color: #e2e8f0; line-height: 1.4; }
.news-meta { font-size: 11px; color: #4a5568; margin-top: 4px; }
</style>
""", unsafe_allow_html=True)


def card(col, label, value=None, change=None, change_fmt="abs"):
    """
    Render a metric card inside a Streamlit column.
    - col: Streamlit column container (from st.columns())
    - label: 小標題
    - value: 主數值（字串，如 "$38.50"，或 None）
    - change: 變動數字（float/int，或 None）
    - change_fmt: "abs" → 顯示具體數字增減 | "pct" → 顯示百分比增減
    """
    with col:
        display_val = value if value is not None else "—"

        # Defensive: force change to numeric (prevents TypeError if string slips through)
        try:
            change_num = float(change) if change is not None else None
        except (ValueError, TypeError):
            change_num = None

        if change_num is not None and change_fmt == "pct":
            arrow = "▲" if change_num > 0 else "▼" if change_num < 0 else "—"
            delta_txt = f"{arrow} {abs(change_num):.2f}%"
            cls = "up" if change_num > 0 else "down" if change_num < 0 else "neutral"
        elif change_num is not None:
            arrow = "▲" if change_num > 0 else "▼" if change_num < 0 else "—"
            delta_txt = f"{arrow} {abs(change_num):,.2f}"
            cls = "up" if change_num > 0 else "down" if change_num < 0 else "neutral"
        else:
            delta_txt = ""
            cls = "neutral"
        st.markdown(f"""
        <div class="card">
            <div class="card-header">{label}</div>
            <div class="card-value">{display_val}</div>
            <div class="card-delta {cls}">{delta_txt}</div>
        </div>
        """, unsafe_allow_html=True)


def section_card(title, content_html):
    st.markdown(f"""
    <div class="section-card">
        <div class="section-title">{title}</div>
        {content_html}
    </div>
    """, unsafe_allow_html=True)


# ── Signal card CSS ───────────────────────────────────────────────
st.markdown("""
<style>
.signal-card {
    background: #1a2035;
    border: 1px solid #252e45;
    border-radius: 12px;
    padding: 14px 16px;
    margin-bottom: 10px;
    cursor: pointer;
    transition: border-color 0.2s;
}
.signal-card:hover {
    border-color: #4a9eff;
}
.signal-tier-1 { border-left: 4px solid #ff6b6b; }
.signal-tier-2 { border-left: 4px solid #ffd93d; }
.signal-tier-3 { border-left: 4px solid #6bcbff; }
.signal-header {
    display: flex; justify-content: space-between; align-items: center;
    margin-bottom: 6px;
}
.signal-symbol { font-size: 16px; font-weight: 700; color: #e2e8f0; }
.signal-tier-badge {
    font-size: 11px; font-weight: 600; padding: 2px 8px;
    border-radius: 10px; letter-spacing: 0.05em;
}
.tier-badge-1 { background: rgba(255,107,107,0.2); color: #ff6b6b; }
.tier-badge-2 { background: rgba(255,217,61,0.2); color: #ffd93d; }
.tier-badge-3 { background: rgba(107,203,255,0.2); color: #6bcbff; }
.signal-sector { font-size: 11px; color: #718096; }
.signal-signal { font-size: 13px; color: #cbd5e0; margin: 4px 0; }
.signal-pattern {
    font-size: 11px; color: #a0aec0;
    background: rgba(255,255,255,0.05); border-radius: 6px;
    padding: 2px 8px; display: inline-block; margin: 2px 0;
}
.signal-meta {
    display: flex; flex-wrap: wrap; gap: 12px; margin-top: 6px;
    font-size: 12px; color: #a0aec0;
}
.signal-meta b { color: #e2e8f0; }
.signal-prices {
    display: flex; gap: 16px; margin-top: 8px; padding-top: 8px;
    border-top: 1px solid #2d3748; font-size: 12px;
}
.signal-tp { color: #48bb78; }
.signal-sl { color: #fc8181; }
.signal-wr { color: #e2e8f0; }
.signal-wr b { color: #48bb78; }
.signal-wr-sector { color: #e2e8f0; }
.signal-wr-sector b { color: #a78bfa; }
.signal-price-now { color: #e2e8f0; font-weight: 600; }
.signal-vol { font-size: 11px; }
.signal-vol-yes { color: #48bb78; }
.signal-vol-no { color: #718096; }
.signal-section-title {
    font-size: 18px; font-weight: 700; color: #e2e8f0;
    margin: 20px 0 10px 0;
    padding-bottom: 8px; border-bottom: 2px solid #2d3748;
}
.signal-section-subtitle {
    font-size: 12px; color: #718096; margin-bottom: 14px;
}
/* ── Clickable card link ── */
a.signal-card-link {
    text-decoration: none !important;
    color: inherit !important;
    display: block;
}
a.signal-card-link:hover .signal-card {
    border-color: #4a9eff;
    box-shadow: 0 0 12px rgba(74, 158, 255, 0.15);
}
/* ── Sector stats ── */
.sector-stats-container {
    display: flex; flex-wrap: wrap; gap: 8px; margin: 16px 0;
}
.sector-stat-item {
    background: #161c2e;
    border: 1px solid #252e45;
    border-radius: 8px;
    padding: 8px 12px;
    min-width: 120px;
    flex: 1 0 auto;
}
.sector-stat-sig { border-left: 3px solid #48bb78; }
.sector-stat-no-sig { border-left: 3px solid #4a5568; opacity: 0.6; }
.sector-stat-name { font-size: 11px; color: #718096; margin-bottom: 4px; }
.sector-stat-bar {
    display: flex; align-items: center; gap: 8px;
}
.sector-stat-bar-fill {
    height: 6px; border-radius: 3px; background: #2d3748; flex: 1; overflow: hidden;
}
.sector-stat-bar-fill-inner {
    height: 100%; border-radius: 3px; background: #48bb78; transition: width 0.3s;
}
.sector-stat-numbers {
    font-size: 11px; color: #a0aec0; white-space: nowrap;
}
.sector-stat-numbers b { color: #e2e8f0; }
</style>
""", unsafe_allow_html=True)


def metric_row(label, value):
    return f'<div class="metric-pair"><span class="metric-label">{label}</span><span class="metric-val">{value}</span></div>'


def news_card_html(title, publisher, date, url):
    return f"""
    <div class="news-card">
        <div class="news-title"><a href="{url}" target="_blank" style="color:#e2e8f0;text-decoration:none">{title}</a></div>
        <div class="news-meta">📰 {publisher} &nbsp;•&nbsp; {date}</div>
    </div>
    """


HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

# ── Database cache layer ────────────────────────────────────────────────────
from database.cache import (
    load_stock_data,
    record_search,
    get_recent_searches,
    get_top_searched,
    suggest_symbols as _suggest_symbols,
)

from pattern_detector import detect_all_patterns, get_latest_patterns
from pattern_annotator import add_pattern_markers, build_pattern_legend

# ── Daily Signals (用於 signal homepage) ────────────────────────────────
from database.signals import load_daily_signals, get_signal_summary, signal_date

import json
from pathlib import Path

# ── Portfolio data engine ──
from database.portfolio import PortfolioManager, Trade
from dynamic_stops_tab import render_dynamic_stops_tab

SIGNALS_DIR = Path(__file__).resolve().parent / "data" / "signals"

def load_sector_counts(market: str = "us") -> list:
    """Load sector total/signal counts from JSON saved by signal_scanner."""
    path = SIGNALS_DIR / f"sector_counts_{market}.json"
    if not path.exists():
        return []
    try:
        with open(path) as f:
            data = json.load(f)
        return data.get("sectors", [])
    except Exception:
        return []


# ─────────────────────────────────────────────
# 股票搜尋函數（使用 DB 緩存的建議 API）
# ─────────────────────────────────────────────

@st.cache_data(ttl=600)
def _get_quarterly_data(ticker: str) -> dict:
    """Cached wrapper: quarterly financial data from yfinance."""
    try:
        stock = yf.Ticker(ticker)
        q_inc = stock.quarterly_income_stmt
        q_bs  = stock.quarterly_balance_sheet
        q_cf  = stock.quarterly_cashflow
        quarters = {}
        if q_inc is not None and not q_inc.empty:
            for col in q_inc.columns[:4]:
                label = pd.to_datetime(col).strftime("%Y-%m-%d")
                ci    = list(q_inc.columns).index(col)
                def sv(series, idx=None):
                    try:
                        v = series.iloc[idx] if idx is not None else series
                        if hasattr(v, 'item'): v = v.item()
                        return float(v) if pd.notna(v) and v is not None else None
                    except Exception:
                        return None
                m = {}
                for row in ["Total Revenue","Net Income","Basic EPS","Diluted EPS",
                             "Operating Income","Operating Profit"]:
                    if row in q_inc.index:
                        val = sv(q_inc.loc[row], ci)
                        if row == "Total Revenue" and "revenue" not in m: m["revenue"] = val
                        elif row == "Net Income"   and "net_income" not in m: m["net_income"] = val
                        elif row in ("Basic EPS","Diluted EPS") and "eps" not in m: m["eps"] = val
                        elif row in ("Operating Income","Operating Profit") and "operating_income" not in m:
                            m["operating_income"] = val
                if q_bs is not None and not q_bs.empty:
                    bi = list(q_bs.columns).index(col)
                    for row, key in [("Stockholders Equity","equity"),
                                      ("Total Debt","total_debt"),
                                      ("Cash And Cash Equivalents","cash"),
                                      ("Total Assets","total_assets")]:
                        if row in q_bs.index:
                            m[key] = sv(q_bs.loc[row], bi)
                    if m.get("equity") and m["equity"] > 0 and m.get("net_income"):
                        m["roe"] = (m["net_income"] / m["equity"]) * 100
                if q_cf is not None and not q_cf.empty:
                    cfi = list(q_cf.columns).index(col)
                    for row, key in [("Operating Cash Flow","operating_cf"),
                                      ("Free Cash Flow","free_cf")]:
                        if row in q_cf.index:
                            m[key] = sv(q_cf.loc[row], cfi)
                def grow(curr, prev):
                    if curr is None or prev is None or prev == 0:
                        return None, "—"
                    chg = ((curr - prev) / abs(prev)) * 100
                    return chg, f"{'↑' if chg >= 0 else '↓'} {abs(chg):.1f}%"
                q_keys = list(quarters.keys())
                prev_m = quarters[q_keys[-1]] if q_keys else None
                m["rev_qoq"] = grow(m.get("revenue"),    prev_m.get("revenue")    if prev_m else None)
                m["ni_qoq"]  = grow(m.get("net_income"), prev_m.get("net_income") if prev_m else None)
                m["eps_qoq"] = grow(m.get("eps"),        prev_m.get("eps")        if prev_m else None)
                m["ocf_qoq"] = grow(m.get("operating_cf"), prev_m.get("operating_cf") if prev_m else None)
                quarters[label] = m
        return quarters
    except Exception:
        return {}


@st.cache_data(ttl=300)
def get_stock_info(ticker: str) -> dict:
    """Cached wrapper: live stock.info from Yahoo Finance."""
    try:
        return yf.Ticker(ticker).info or {}
    except Exception:
        return {}

def search_tickers(query: str) -> list:
    """Wrapper: delegate to cache layer (Yahoo Finance suggestions API)."""
    raw = _suggest_symbols(query)
    return [{"symbol": r["symbol"], "name": r["name"], "exchange": r.get("exch", "")}
            for r in raw]


def format_suggestion(q: dict) -> str:
    """為 selectbox 格式化顯示：SYMBOL — Full Name (EXCH)"""
    sym = q["symbol"]
    name = q["name"]
    exch = q.get("exchange", "")
    return f"{sym} — {name} ({exch})"


# ─────────────────────────────────────────────
# 緩存數據函數
# ─────────────────────────────────────────────

@st.cache_data(ttl=300)
def get_stock_data(ticker: str, days: int = 90) -> pd.DataFrame:
    """
    讀取 K 線數據：優先從本地 DuckDB，Yahoo Finance 為後備。
    DuckDB 策略：
      - 平日：當日收盤後 08:30 / 20:30 UTC 更新，覆蓋約 16.5 小時前的數據
      - 若 DB 完全空白或行數不足，自動降級至 Yahoo Finance 即時拉取
    """
    # ── 優先：從 DuckDB 讀取 ──────────────────────────────────────────
    try:
        from database.duckdb_client import get_price_range
        rows = get_price_range(ticker, days=days)
        if rows and len(rows) >= days * 0.7:   # 起碼有 7 成數據
            df = pd.DataFrame(rows)
            df = df.rename(columns={
                "trade_date": "Date",
                "open": "Open", "high": "High",
                "low": "Low",  "close": "Close", "volume": "Volume"
            })
            df["Date"] = pd.to_datetime(df["Date"])
            df = df.set_index("Date").sort_index()
            # 確保走勢方向正確（newest first）
            return df.iloc[::-1][["Open", "High", "Low", "Close", "Volume"]]
    except Exception:
        pass

    # ── 後備：Yahoo Finance 即時拉取 ────────────────────────────────
    try:
        # 優先使用 Yahoo v8 REST API（無 auto_adjust NaN 問題）
        import requests as _req
        api_ticker = ticker.replace(".", "-")
        url = f"https://query2.finance.yahoo.com/v8/finance/chart/{api_ticker}"
        params = {"interval": "1d", "range": f"{days}d"}
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = _req.get(url, params=params, headers=headers, timeout=10)
        data = resp.json()
        result = data.get("chart", {}).get("result", [])
        if result:
            timestamps = result[0]["timestamp"]
            ohlcv = result[0]["indicators"]["quote"][0]
            df = pd.DataFrame(ohlcv, index=pd.to_datetime(timestamps, unit="s"))
            df.index = df.index.tz_localize(None)
            # v8 API 最新交易日的 close 可能為 None（調整未就緒）
            # 用 yfinance fast_info 補上即時收盤價（比 info 更快）
            if df["close"].iloc[-1] is None or pd.isna(df["close"].iloc[-1]):
                try:
                    _fi = yf.Ticker(ticker).fast_info
                    cur_price = getattr(_fi, "last_price", None)
                    if cur_price and not (isinstance(cur_price, float) and cur_price != cur_price):
                        df.loc[df.index[-1], "close"] = cur_price
                except Exception:
                    pass
            df = df.dropna(subset=["close"])
            if not df.empty:
                return df.rename(columns={
                    "open": "open", "high": "high", "low": "low",
                    "close": "close", "volume": "volume"
                })[["open", "high", "low", "close", "volume"]]
    except Exception:
        pass

    # 最後備用：yfinance
    try:
        df = yf.Ticker(ticker).history(period=f"{days}d", auto_adjust=True)
        if df.empty:
            return pd.DataFrame()
        return df[["Open", "High", "Low", "Close", "Volume"]].rename(
            columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"}
        )
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=60)
def get_realtime_quote(ticker: str) -> dict:
    try:
        stock = yf.Ticker(ticker)
        info = stock.fast_info
        price = info.last_price
        hist = stock.history(period="2d")
        if len(hist) >= 2:
            prev = hist["Close"].iloc[-2]
            change = price - prev
            change_pct = (change / prev) * 100
        else:
            change, change_pct = None, None
        volume = hist["Volume"].iloc[-1] if len(hist) >= 1 else None
        return {"price": price, "change": change, "change_pct": change_pct, "volume": volume}
    except Exception:
        return {"price": None, "change": None, "change_pct": None, "volume": None}


@st.cache_data(ttl=300)
def get_stock_news(ticker: str) -> list:
    """
    使用 Google News RSS 獲取個股相關新聞。
    """
    import re
    from urllib.parse import quote

    # 根據 ticker 類型決定搜尋關鍵字
    if ticker.endswith(".HK"):
        query_en = f"{ticker}"
        query_cn = quote(ticker.replace(".HK", ""))
    elif ticker.endswith(".T") or ticker.endswith(".JP"):
        query = quote(ticker)
        url = f"https://news.google.com/rss/search?q={query}&hl=ja-JP&gl=JP&ceid=JP:ja"
        query_cn = None
    else:
        # 美股：用公司名搜
        try:
            info = yf.Ticker(ticker).info
            name = info.get("longName") or info.get("shortName") or ticker
            query_en = quote(name)
            query_cn = None
        except Exception:
            query_en = quote(ticker)
            query_cn = None

    results = []
    seen_titles = set()

    def parse_google_rss(xml_text: str) -> list:
        items = re.findall(r"<item>(.*?)</item>", xml_text, re.DOTALL)
        parsed = []
        for item in items:
            t_match = re.search(r"<title><!\[CDATA\[(.*?)\]\]></title>", item)
            if not t_match:
                t_match = re.search(r"<title>(.*?)</title>", item)
            d_match = re.search(r"<pubDate>(.*?)</pubDate>", item)
            l_match = re.search(r"<link>(.*?)</link>", item)
            s_match = re.search(r"<source[^>]*>(.*?)</source>", item)
            if not t_match:
                continue
            title = t_match.group(1).strip()
            if not title or title in seen_titles:
                continue
            seen_titles.add(title)
            date_raw = d_match.group(1)[:16] if d_match else ""
            try:
                date = datetime.strptime(date_raw, "%d %b %Y %H:%M").strftime("%Y-%m-%d")
            except Exception:
                date = date_raw[:10] if date_raw else "N/A"
            raw_link = l_match.group(1).strip() if l_match else ""
            if "news.google.com/rss/articles/" in raw_link:
                aid = re.search(r"articles/([^\?]*)", raw_link)
                clean_url = f"https://news.google.com/articles/{aid.group(1)}" if aid else raw_link
            else:
                clean_url = raw_link
            pub = s_match.group(1).strip() if s_match else "Google News"
            parsed.append({"title": title, "date": date, "publisher": pub, "url": clean_url})
        return parsed

    # 英文新聞
    if query_en:
        en_url = f"https://news.google.com/rss/search?q={query_en}&hl=en-US&gl=US&ceid=US:en"
        try:
            resp = requests.get(en_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            if resp.status_code == 200:
                results.extend(parse_google_rss(resp.text))
        except Exception:
            pass

    # 中文新聞（針對 HK/中國股票）
    if query_cn:
        cn_url = f"https://news.google.com/rss/search?q={query_cn}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
        try:
            resp = requests.get(cn_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            if resp.status_code == 200:
                results.extend(parse_google_rss(resp.text))
        except Exception:
            pass

    return results[:8]


@st.cache_data(ttl=600)
def get_quarterly_metrics(ticker: str) -> dict:
    """從 yfinance 取得財務指標和近 4 期季度數據。"""
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        q_inc = stock.quarterly_income_stmt
        q_bs = stock.quarterly_balance_sheet
        q_cf = stock.quarterly_cashflow
        quarters = {}

        if q_inc is not None and not q_inc.empty:
            cols = q_inc.columns[:4].tolist()
            for col in cols:
                label = pd.to_datetime(col).strftime("%Y-%m-%d")
                ci = list(q_inc.columns).index(col)

                def sv(series, col_idx=None):
                    try:
                        v = series.iloc[col_idx] if col_idx is not None else series
                        if hasattr(v, 'item'):
                            v = v.item()
                        return float(v) if pd.notna(v) and v is not None else None
                    except Exception:
                        return None

                m = {}
                for row_name in ["Total Revenue", "Net Income", "Basic EPS", "Diluted EPS",
                                 "Operating Income", "Operating Profit"]:
                    if row_name in q_inc.index:
                        val = sv(q_inc.loc[row_name], ci)
                        if row_name == "Total Revenue" and "revenue" not in m:
                            m["revenue"] = val
                        elif row_name == "Net Income" and "net_income" not in m:
                            m["net_income"] = val
                        elif row_name in ("Basic EPS", "Diluted EPS") and "eps" not in m:
                            m["eps"] = val
                        elif row_name in ("Operating Income", "Operating Profit") and "operating_income" not in m:
                            m["operating_income"] = val

                if q_bs is not None and not q_bs.empty:
                    bi = list(q_bs.columns).index(col)
                    for row_name, key in [
                        ("Stockholders Equity", "equity"),
                        ("Total Debt", "total_debt"),
                        ("Cash And Cash Equivalents", "cash"),
                        ("Total Assets", "total_assets"),
                    ]:
                        if row_name in q_bs.index:
                            m[key] = sv(q_bs.loc[row_name], bi)
                    if m.get("equity") and m["equity"] > 0 and m.get("net_income"):
                        m["roe"] = (m["net_income"] / m["equity"]) * 100

                if q_cf is not None and not q_cf.empty:
                    cfi = list(q_cf.columns).index(col)
                    for row_name, key in [
                        ("Operating Cash Flow", "operating_cf"),
                        ("Free Cash Flow", "free_cf"),
                    ]:
                        if row_name in q_cf.index:
                            m[key] = sv(q_cf.loc[row_name], cfi)

                quarters[label] = m

        def grow(curr, prev):
            if curr is None or prev is None or prev == 0:
                return None, "—"
            chg = ((curr - prev) / abs(prev)) * 100
            return chg, f"{'↑' if chg >= 0 else '↓'} {abs(chg):.1f}%"

        q_keys = list(quarters.keys())
        for i, label in enumerate(q_keys):
            prev = quarters[q_keys[i - 1]] if i > 0 else None
            q = quarters[label]
            q["rev_qoq"] = grow(q.get("revenue"), prev.get("revenue") if prev else None)
            q["ni_qoq"]  = grow(q.get("net_income"), prev.get("net_income") if prev else None)
            q["eps_qoq"] = grow(q.get("eps"), prev.get("eps") if prev else None)
            q["ocf_qoq"] = grow(q.get("operating_cf"), prev.get("operating_cf") if prev else None)

        return {"quarters": quarters, "info": _info_metrics(info)}
    except Exception:
        return {"quarters": {}, "info": {}}


def _info_metrics(info: dict) -> dict:
    def f(key, pct=False, mult=1):
        v = info.get(key)
        if v is None:
            return None
        try:
            return round(float(v) * mult, 2) if pct else round(float(v) * mult, 4)
        except Exception:
            return None
    return {
        "current_price":      info.get("currentPrice"),
        "market_cap":         info.get("marketCap"),
        "trailing_pe":        f("trailingPE"),
        "forward_pe":         f("forwardPE"),
        "price_to_book":      f("priceToBook"),
        "peg_ratio":          f("trailingPegRatio"),
        "trailing_eps":       f("trailingEps"),
        "forward_eps":        f("forwardEps"),
        "roe":                f("returnOnEquity", pct=True),
        "roa":                f("returnOnAssets", pct=True),
        "profit_margin":      f("profitMargins", pct=True),
        "operating_margin":   f("operatingMargins", pct=True),
        "revenue_growth":     f("revenueGrowth", pct=True),
        "earnings_growth":    f("earningsGrowth", pct=True),
        "dividend_yield":     f("dividendYield", pct=True),
        "dividend_rate":      info.get("dividendRate"),
        "beta":               info.get("beta"),
        "total_debt":         info.get("totalDebt"),
        "total_revenue":      info.get("totalRevenue"),
        "52w_high":           info.get("fiftyTwoWeekHigh"),
        "52w_low":            info.get("fiftyTwoWeekLow"),
        "recommendation":     info.get("recommendationKey"),
        "analyst_count":      info.get("numberOfAnalystOpinions"),
        "target_mean_price":  info.get("targetMeanPrice"),
        "company_name":       info.get("longName") or info.get("shortName", ""),
    }


# ─────────────────────────────────────────────
# 圖表函數
# ─────────────────────────────────────────────

def _build_rangebreaks(ticker: str, df: pd.DataFrame) -> list:
    """
    Build Plotly rangebreaks list to hide non-trading days (weekends + market holidays).
    This compresses gaps visually WITHOUT removing data points.
    """
    market = get_market_from_ticker(ticker)
    holidays_set = HKEX_HOLIDAYS if market == "HK" else US_HOLIDAYS

    breaks = []
    # Always hide weekends
    breaks.append(dict(bounds=[5, 7], pattern="day of week"))

    # Hide known market holidays
    holiday_dates = [
        d for d in df.index
        if d.date() in holidays_set
    ]
    for d in holiday_dates:
        breaks.append(dict(bounds=[d, d], pattern="day of week"))

    return breaks


def _make_date_axis_config(dates, n_ticks: int = 12) -> dict:
    """
    Return a Plotly xaxis config using integer positions (no gaps) with real dates as tick labels.
    dates: list/Series of datetime objects (same length as data).
    """
    n = len(dates)
    step = max(1, n // n_ticks)
    tick_vals = list(range(0, n, step))
    tick_texts = [dates[i].strftime('%Y-%m-%d') for i in tick_vals]
    return dict(
        tickmode='array',
        tickvals=tick_vals,
        ticktext=tick_texts,
        showgrid=True,
        gridcolor='rgba(255,255,255,0.05)',
        rangeslider=dict(visible=False),
    )


def plot_candlestick(df, ticker, company_name="", patterns=None, show_bb=True):
    """Candlestick chart: integer x-axis (no gaps) with date labels + pattern markers + optional BB bands."""
    if patterns is None:
        patterns = []
    df = df.dropna(subset=["open", "high", "low", "close"]).copy()
    df = df.sort_index()
    dates = df.index.tolist()
    df = df.reset_index(drop=True)
    title = f"{ticker}" + (f" — {company_name}" if company_name else "")

    fig = go.Figure(data=[go.Candlestick(
        x=list(range(len(df))),
        open=df["open"], high=df["high"], low=df["low"], close=df["close"],
        name="OHLC", xhoverformat="%Y-%m-%d",
        text=dates, hovertemplate='%{text}<br>O: %{customdata[0]:.2f} H: %{customdata[1]:.2f} L: %{customdata[2]:.2f} C: %{customdata[3]:.2f}<extra></extra>',
        customdata=df[["open","high","low","close"]].values,
    )])

    # Bollinger Bands
    if show_bb and "bb_upper" in df.columns and "bb_lower" in df.columns and "bb_middle" in df.columns:
        idx = list(range(len(df)))
        fig.add_trace(go.Scatter(
            x=idx, y=df["bb_upper"], name="BB Upper",
            line=dict(color="rgba(130,90,200,0.6)", width=1, dash="dot"),
            text=dates, hovertemplate='%{text}<br>BB Upper: %{y:.2f}<extra></extra>',
        ))
        fig.add_trace(go.Scatter(
            x=idx, y=df["bb_middle"], name="BB Middle",
            line=dict(color="rgba(130,90,200,0.4)", width=0.8, dash="dash"),
            text=dates, hovertemplate='%{text}<br>BB Middle: %{y:.2f}<extra></extra>',
        ))
        fig.add_trace(go.Scatter(
            x=idx, y=df["bb_lower"], name="BB Lower",
            line=dict(color="rgba(130,90,200,0.6)", width=1, dash="dot"),
            text=dates, hovertemplate='%{text}<br>BB Lower: %{y:.2f}<extra></extra>',
            fill='tonexty', fillcolor='rgba(130,90,200,0.05)',
        ))

    xaxis_cfg = _make_date_axis_config(dates)
    xaxis_cfg['rangeslider']['visible'] = False
    fig.update_layout(
        title=title, yaxis_title="HKD / USD",
        xaxis_title="日期", template="plotly_dark",
        height=460, xaxis=xaxis_cfg,
    )
    fig = add_pattern_markers(fig, df, patterns, dates)
    return fig


def plot_line(df, ticker, company_name=""):
    """Line chart: integer x-axis (no gaps) with date labels."""
    df = df.dropna(subset=["close"]).copy()
    df = df.sort_index()
    dates = df.index.tolist()
    df = df.reset_index(drop=True)
    title = f"{ticker}" + (f" — {company_name}" if company_name else "")
    xaxis_cfg = _make_date_axis_config(dates)
    fig = go.Figure([go.Scatter(
        x=list(range(len(df))), y=df["close"], mode="lines", name="收盤價",
        line=dict(color="#00d4ff", width=2),
        text=dates, hovertemplate='%{text}<br>$%{y:.2f}<extra></extra>',
    )])
    fig.update_layout(
        title=title, yaxis_title="HKD / USD",
        xaxis_title="日期", template="plotly_dark", height=360,
        xaxis=xaxis_cfg,
    )
    return fig


def plot_volume(df, ticker):
    """Volume bar chart: integer x-axis (no gaps) with date labels."""
    df = df.dropna(subset=["volume", "close", "open"]).copy()
    df = df.sort_index()
    dates = df.index.tolist()
    df = df.reset_index(drop=True)
    xaxis_cfg = _make_date_axis_config(dates)
    colors = ["green" if row["close"] >= row["open"] else "red" for _, row in df.iterrows()]
    fig = go.Figure(data=[go.Bar(
        x=list(range(len(df))), y=df["volume"],
        marker_color=colors, name="成交量",
        text=dates, hovertemplate='%{text}<br>Vol: %{y:,}<extra></extra>',
    )])
    fig.update_layout(
        title=f"{ticker} 成交量", yaxis_title="成交量", xaxis_title="日期",
        template="plotly_dark", height=200,
        xaxis=xaxis_cfg,
    )
    return fig

def plot_rsi(df, ticker):
    """RSI(14) subplot: oscillator with overbought/oversold zones."""
    if "rsi_14" not in df.columns:
        return go.Figure()
    df = df.dropna(subset=["rsi_14"]).copy()
    df = df.sort_index()
    dates = df.index.tolist()
    df = df.reset_index(drop=True)
    idx = list(range(len(df)))
    rsi = df["rsi_14"]

    colors = ["#48bb78" if v < 30 else "#fc8181" if v > 70 else "#a0aec0" for v in rsi]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=idx, y=rsi, name="RSI(14)",
        line=dict(color="#00d4ff", width=1.5),
        text=dates, hovertemplate='%{text}<br>RSI: %{y:.1f}<extra></extra>',
    ))
    # Overbought zone
    fig.add_hrect(y0=70, y1=100, fillcolor="rgba(252,129,129,0.12)", line_width=0,
                  annotation_text="Overbought", annotation_position="top right",
                  annotation_font_color="#fc8181")
    # Oversold zone
    fig.add_hrect(y0=0, y1=30, fillcolor="rgba(72,187,120,0.12)", line_width=0,
                  annotation_text="Oversold", annotation_position="bottom right",
                  annotation_font_color="#48bb78")
    # 50 line
    fig.add_hline(y=50, line=dict(color="rgba(160,174,192,0.3)", width=0.8, dash="dash"))
    fig.add_hline(y=30, line=dict(color="rgba(72,187,120,0.4)", width=0.8, dash="dot"))
    fig.add_hline(y=70, line=dict(color="rgba(252,129,129,0.4)", width=0.8, dash="dot"))

    fig.update_layout(
        title=f"{ticker} RSI(14)", yaxis_title="RSI",
        xaxis_title="日期", template="plotly_dark", height=220,
        xaxis=_make_date_axis_config(dates),
        yaxis=dict(range=[0, 100]),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig


def plot_macd(df, ticker):
    """MACD subplot: MACD line, signal line, and histogram."""
    if "macd" not in df.columns or "macd_signal" not in df.columns:
        return go.Figure()
    df = df.dropna(subset=["macd", "macd_signal"]).copy()
    df = df.sort_index()
    dates = df.index.tolist()
    df = df.reset_index(drop=True)
    idx = list(range(len(df)))

    hist_colors = ["#48bb78" if v >= 0 else "#fc8181" for v in df["macd_histogram"]]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=idx, y=df["macd_histogram"], name="Histogram",
        marker_color=hist_colors, opacity=0.7,
        text=dates, hovertemplate='%{text}<br>Hist: %{y:.4f}<extra></extra>',
    ))
    fig.add_trace(go.Scatter(
        x=idx, y=df["macd"], name="MACD",
        line=dict(color="#00d4ff", width=1.5),
        text=dates, hovertemplate='%{text}<br>MACD: %{y:.4f}<extra></extra>',
    ))
    fig.add_trace(go.Scatter(
        x=idx, y=df["macd_signal"], name="Signal",
        line=dict(color="#f6e05e", width=1.2),
        text=dates, hovertemplate='%{text}<br>Signal: %{y:.4f}<extra></extra>',
    ))
    fig.add_hline(y=0, line=dict(color="rgba(160,174,192,0.4)", width=0.8))

    fig.update_layout(
        title=f"{ticker} MACD (12,26,9)", yaxis_title="MACD",
        xaxis_title="日期", template="plotly_dark", height=220,
        xaxis=_make_date_axis_config(dates),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig


def plot_kdj(df, ticker):
    """KDJ subplot: K, D, J lines with overbought/oversold zones."""
    if "kdj_k" not in df.columns or "kdj_d" not in df.columns or "kdj_j" not in df.columns:
        return go.Figure()
    df = df.dropna(subset=["kdj_k", "kdj_d", "kdj_j"]).copy()
    df = df.sort_index()
    dates = df.index.tolist()
    df = df.reset_index(drop=True)
    idx = list(range(len(df)))

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=idx, y=df["kdj_k"], name="K",
        line=dict(color="#00d4ff", width=1.5),
        text=dates, hovertemplate='%{text}<br>K: %{y:.1f}<extra></extra>',
    ))
    fig.add_trace(go.Scatter(
        x=idx, y=df["kdj_d"], name="D",
        line=dict(color="#f6e05e", width=1.2),
        text=dates, hovertemplate='%{text}<br>D: %{y:.1f}<extra></extra>',
    ))
    fig.add_trace(go.Scatter(
        x=idx, y=df["kdj_j"], name="J",
        line=dict(color="#ed64a6", width=1.0, dash="dot"),
        text=dates, hovertemplate='%{text}<br>J: %{y:.1f}<extra></extra>',
    ))
    # Overbought zone
    fig.add_hrect(y0=80, y1=100, fillcolor="rgba(252,129,129,0.10)", line_width=0,
                  annotation_text="Overbought", annotation_position="top right",
                  annotation_font_color="#fc8181")
    # Oversold zone
    fig.add_hrect(y0=0, y1=20, fillcolor="rgba(72,187,120,0.10)", line_width=0,
                  annotation_text="Oversold", annotation_position="bottom right",
                  annotation_font_color="#48bb78")
    fig.add_hline(y=80, line=dict(color="rgba(252,129,129,0.4)", width=0.8, dash="dot"))
    fig.add_hline(y=20, line=dict(color="rgba(72,187,120,0.4)", width=0.8, dash="dot"))

    fig.update_layout(
        title=f"{ticker} KDJ(9)", yaxis_title="KDJ",
        xaxis_title="日期", template="plotly_dark", height=220,
        xaxis=_make_date_axis_config(dates),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig


def fmt_hkd(val):
    if val is None:
        return "—"
    if abs(val) >= 1e9:
        return f"${val/1e9:.2f}B"
    elif abs(val) >= 1e6:
        return f"${val/1e6:.2f}M"
    else:
        return f"${val:,.0f}"


def fmt_pct(val):
    if val is None:
        return "—"
    return f"{val:.2f}%"


def m4(col, label, value, delta=None):
    with col:
        if delta:
            st.metric(label, value, delta=delta)
        else:
            st.metric(label, value)


# ─────────────────────────────────────────────
# Main UI
# ─────────────────────────────────────────────

st.title("📈 Stock Dashboard")


# ──────────────── Signal Homepage ────────────────
def render_signal_homepage():
    """Render the daily signals homepage with clickable signal cards."""
    df_us = load_daily_signals("us")
    df_hk = load_daily_signals("hk")

    if df_us.empty and df_hk.empty:
        st.info("📡 今日無信號數據。信號掃描將在交易日自動生成。")
        return

    sig_date = signal_date() or "今日"

    # ── Summary stats ──
    col_s, _, _ = st.columns([1, 1, 1])
    with col_s:
        total = len(df_us) + len(df_hk)

        def _tier_count(df, tier):
            if df.empty or "tier" not in df.columns:
                return 0
            return int((df["tier"] == tier).sum())

        t1 = _tier_count(df_us, 1) + _tier_count(df_hk, 1)
        t2 = _tier_count(df_us, 2) + _tier_count(df_hk, 2)
        t3 = total - t1 - t2
        st.markdown(f"<div class='card'><div class='card-header'>📡 信號總覽 ({sig_date})</div>"
                    f"<div class='card-value'>共 {total} 個</div>"
                    f"<div class='card-delta'>🔥 Tier-1: {t1} ｜ ⚡ Tier-2: {t2} ｜ 📊 Tier-3: {t3}</div></div>",
                    unsafe_allow_html=True)

    # ── Sector breakdown ──
    _render_sector_breakdown("us", "🇺🇸 美股板塊")
    _render_sector_breakdown("hk", "🇭🇰 港股板塊")

    st.markdown("---")

    # ── Tabs for US / HK ──
    tab_us, tab_hk = st.tabs(["🇺🇸 美股信號", "🇭🇰 港股信號"])

    with tab_us:
        _render_signal_tab(df_us, "us")

    with tab_hk:
        _render_signal_tab(df_hk, "hk")


def _render_sector_breakdown(market: str, label: str):
    """Render a compact sector breakdown bar for one market."""
    sectors = load_sector_counts(market)
    if not sectors:
        return

    # Only show sectors that have either stocks or signals
    sectors = [s for s in sectors if s["total_stocks"] > 0]
    if not sectors:
        return

    # Sort: has signals first, then by signal_count desc
    sectors.sort(key=lambda s: (-(1 if s["signal_count"] > 0 else 0), -s["signal_count"]))

    html = f'<div class="card"><div class="card-header">{label} — 板塊分布</div><div class="sector-stats-container">'
    for s in sectors:
        pct = (s["signal_count"] / s["total_stocks"] * 100) if s["total_stocks"] > 0 else 0
        cls = "sector-stat-sig" if s["signal_count"] > 0 else "sector-stat-no-sig"
        html += f"""
        <div class="sector-stat-item {cls}">
            <div class="sector-stat-name">{s["sector"]}</div>
            <div class="sector-stat-bar">
                <div class="sector-stat-bar-fill">
                    <div class="sector-stat-bar-fill-inner" style="width:{pct:.0f}%"></div>
                </div>
                <div class="sector-stat-numbers">
                    <b>{s["signal_count"]}</b>/{s["total_stocks"]}
                </div>
            </div>
        </div>"""
    html += '</div></div>'
    st.markdown(html, unsafe_allow_html=True)


def _get_latest_prices_batch(symbols: list) -> dict:
    """Preload latest close prices from DuckDB for a list of symbols.
    Returns {symbol: price} dict; missing symbols are omitted."""
    try:
        from database.duckdb_client import PRICES_DB
        import duckdb
        con = duckdb.connect(PRICES_DB, read_only=True)
        # Build a single query with all symbols
        placeholders = ",".join("?" for _ in symbols)
        rows = con.execute(f"""
            SELECT symbol, close
            FROM stock_prices
            WHERE symbol IN ({placeholders})
              AND trade_date = (SELECT MAX(trade_date) FROM stock_prices WHERE symbol IN ({placeholders}))
        """, symbols * 2).fetchall()
        con.close()
        return {r[0]: float(r[1]) for r in rows}
    except Exception:
        return {}


def _render_signal_tab(df: pd.DataFrame, market: str):
    """Render signal cards for one market tab."""
    if df.empty:
        st.caption("📭 暫無信號")
        return

    # Sort: tier asc, win_rate desc
    df = df.sort_values(["tier", "win_rate"], ascending=[True, False]).reset_index(drop=True)

    # Preload latest prices from DB for all symbols in this tab
    symbols = df["symbol"].dropna().unique().tolist()
    latest_prices = _get_latest_prices_batch(symbols)

    tier_labels = {1: "🔥 Tier-1 強烈買入信號", 2: "⚡ Tier-2 買入信號", 3: "📊 Tier-3 觀察信號"}
    tier_classes = {1: "signal-tier-1", 2: "signal-tier-2", 3: "signal-tier-3"}
    tier_badges = {1: "tier-badge-1", 2: "tier-badge-2", 3: "tier-badge-3"}

    for tier in [1, 2, 3]:
        tier_df = df[df["tier"] == tier]
        if tier_df.empty:
            continue

        st.markdown(f"<div class='signal-section-title'>{tier_labels[tier]}</div>"
                    f"<div class='signal-section-subtitle'>{len(tier_df)} 個信號</div>",
                    unsafe_allow_html=True)

        # Two-column layout for signal cards
        cols = st.columns(2)
        for i, (_, row) in enumerate(tier_df.iterrows()):
            with cols[i % 2]:
                _render_signal_card(row, market, tier, tier_classes, tier_badges, latest_prices)


def _render_signal_card(row: pd.Series, market: str, tier: int,
                        tier_classes: dict, tier_badges: dict,
                        latest_prices: dict = None):
    """Render a single clickable signal card. Clicking navigates to stock detail page."""
    symbol = row.get("symbol", "?")
    sector = row.get("sector", "?")
    signal_name = row.get("signal", "?")
    pattern = row.get("pattern", "None")
    pattern_conf = row.get("pattern_conf", 0)

    price = row.get("price", 0)
    sig_date = row.get("date", "")
    wr_stock = row.get("win_rate_stock")
    wr_sector = row.get("win_rate_sector")
    stock_n = int(row.get("stock_n", 0))
    sector_n = int(row.get("sector_n", 0))
    avg_ret = row.get("avg_return", 0)
    vol_conf = bool(row.get("volume_confirmed", False))

    tp1 = row.get("tp1_price", 0)
    tp2 = row.get("tp2_price", 0)
    sl = row.get("sl_price", 0)

    price_prefix = "HK$" if market == "hk" else "$"

    # Signal entry price
    price_str = f"{price_prefix}{price:.2f}" if price else "—"

    # Current/live price from DB
    current_price = (latest_prices or {}).get(symbol)
    if current_price:
        current_str = f"{price_prefix}{current_price:.2f}"
        price_diff = current_price - price if price else 0
        diff_pct = (price_diff / price * 100) if price and price > 0 else 0
        diff_color = "#48bb78" if price_diff >= 0 else "#fc8181"
        diff_arrow = "↑" if price_diff >= 0 else "↓"
        current_html = f'<span style="color:{diff_color};font-weight:600">{current_str}</span> <span style="color:{diff_color};font-size:11px">{diff_arrow} {abs(diff_pct):.1f}%</span>'
    else:
        current_html = f'<span style="color:#718096">—</span>'

    date_str = str(sig_date) if sig_date else "—"

    tp1_str = f"{price_prefix}{tp1:.2f}" if tp1 else "—"
    tp2_str = f"{price_prefix}{tp2:.2f}" if tp2 else "—"
    sl_str = f"{price_prefix}{sl:.2f}" if sl else "—"

    # Win rate display — split into individual components
    def _fmt_wr(val):
        if val is None or (isinstance(val, float) and val != val) or val <= 0:
            return "—"
        return f"{val:.0%}"

    stock_wr_str = _fmt_wr(wr_stock)
    sector_wr_str = _fmt_wr(wr_sector)
    stock_n_str = f"n={stock_n}" if stock_n > 0 else ""
    sector_n_str = f"n={sector_n}" if sector_n > 0 else ""

    # Confidence indicator
    overall_wr = row.get("win_rate", 0)
    flag = "🟢" if overall_wr >= 0.60 else ("🟡" if overall_wr >= 0.45 else "⚪")
    vol_icon = "📈" if vol_conf else "⚪"

    card_html = f"""
    <a href="?nav_ticker={symbol}" class="signal-card-link">
    <div class="signal-card {tier_classes.get(tier, '')}">
        <div class="signal-header">
            <span class="signal-symbol">{flag} {vol_icon} {symbol}</span>
            <span class="signal-tier-badge {tier_badges.get(tier, '')}">Tier-{tier}</span>
        </div>
        <div class="signal-sector">🏭 {sector[:20]} ｜ 📅 {date_str}</div>
        <div class="signal-signal">{signal_name}</div>
        <div class="signal-pattern">📐 形態確認: {pattern} {f'({pattern_conf:.0%})' if pattern and pattern != 'None' else ''}</div>
        <div class="signal-meta">
            <span>💰 信號價: <b>{price_str}</b></span>
            <span>📍 現價: {current_html}</span>
        </div>
        <div class="signal-meta" style="border-top: none; padding-top: 4px;">
            <span>📈 回報: <b>{avg_ret:+.1%}</b></span>
            <span></span>
        </div>
        <div class="signal-prices">
            <span class="signal-wr">📊 個股勝率: <b>{stock_wr_str}</b> {stock_n_str}</span>
            <span class="signal-wr-sector">🏢 板塊勝率: <b>{sector_wr_str}</b> {sector_n_str}</span>
        </div>
        <div class="signal-prices" style="border-top: none; padding-top: 4px;">
            <span class="signal-tp">🎯 TP1: {tp1_str}</span>
            <span class="signal-tp">🎯 TP2: {tp2_str}</span>
            <span class="signal-sl">🛑 SL: {sl_str}</span>
        </div>
    </div>
    </a>
    """

    st.markdown(card_html, unsafe_allow_html=True)



# ── 頁面選擇 ──
st.sidebar.markdown(
    "<div style='background:#2d3748; border-radius:8px; padding:8px; margin-bottom:6px; text-align:center; font-weight:600;'>📋 功能選單</div>",
    unsafe_allow_html=True,
)
if st.sidebar.button("📡 信號看板", use_container_width=True, key="nav_sig", type="secondary"):
    st.session_state["app_page"] = "signal"
    st.rerun()
if st.sidebar.button("🗂️ 持倉管理", use_container_width=True, key="nav_prt", type="primary"):
    st.session_state["app_page"] = "portfolio"
    st.rerun()
if st.sidebar.button("🛡️ 止損監控", use_container_width=True, key="nav_stop", type="secondary"):
    st.session_state["app_page"] = "stops"
    st.rerun()
page = st.session_state.get("app_page", "signal")
page_label = "📡 信號看板" if page == "signal" else ("🗂️ 持倉管理" if page == "portfolio" else "🛡️ 止損監控")

# ── 側邊欄：股票搜尋（僅在信號看板頁面顯示）──
if page == "signal":
    st.sidebar.header("🔍 股票搜尋")
    search_query = st.sidebar.text_input(
        "輸入股票代碼或名稱",
        placeholder="例如：TSLA、3968.HK、AAPL",
        help="輸入關鍵字後，系統會自動顯示相關股票建議"
    )

    # 搜尋建議
    suggestions = []
    if search_query:
        suggestions = search_tickers(search_query)

    selected_ticker = None

    if suggestions:
        options = [format_suggestion(q) for q in suggestions]
        selected = st.sidebar.selectbox(
            "選擇股票",
            options=options,
            label_visibility="collapsed"
        )
        idx = options.index(selected)
        selected_ticker = suggestions[idx]["symbol"]
    elif search_query:
        st.sidebar.warning(f"找不到「{search_query}」，請嘗試其他關鍵字")

    # 如果沒有輸入且不是從信號按鈕跳轉，顯示信號首頁
    if not selected_ticker:
        if "navigate_to_ticker" in st.session_state and st.session_state.navigate_to_ticker:
            selected_ticker = st.session_state.pop("navigate_to_ticker")
        else:
            st.sidebar.caption("💡 輸入股票代碼搜尋，或瀏覽下方信號")
            raw = search_query.strip().upper()
            if raw and ( "." in raw or raw.isalpha() ):
                selected_ticker = raw

    days_range = st.sidebar.slider("顯示天數", 30, 365, 90)

    refresh_key = f"refresh_{selected_ticker}"
    col1, col2 = st.sidebar.columns([1, 1])
    with col1:
        st.write("")
    with col2:
        if st.button("🔄 刷新數據", key=refresh_key):
            st.cache_data.clear()
            data = load_stock_data(selected_ticker, force_refresh=True)
            st.rerun()

    # ── 信號首頁（無搜尋時顯示）──
    if not selected_ticker:
        render_signal_homepage()
        st.markdown("---")
        st.caption(f"💡 在側邊欄輸入股票代碼可查看個股詳情 | 最後更新: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        st.stop()

@st.cache_resource
def _get_portfolio_manager() -> PortfolioManager:
    return PortfolioManager()


def _render_portfolio_tab():
    pm = _get_portfolio_manager()

    st.markdown("## 🗂️ 持倉管理")

    col_a1, col_a2, _ = st.columns([2, 2, 3])
    with col_a1:
        with st.popover("➕ 新增持倉", use_container_width=True):
            _render_add_position_form(pm)
    with col_a2:
        with st.popover("📅 日期區間回報", use_container_width=True):
            _render_date_range_form(pm)

    st.markdown("---")
    _render_portfolio_overview(pm)
    st.markdown("---")
    _render_open_positions(pm)
    st.markdown("---")
    _render_closed_positions(pm)


def _render_portfolio_overview(pm: PortfolioManager):
    open_pos = pm.get_open_positions()
    total_cost = 0.0
    total_market = 0.0
    rows = []
    for pos in open_pos:
        r = pm.get_unrealized_pnl(pos.id)
        if r:
            total_cost += r["cost_basis"]
            total_market += r["market_value"]
            rows.append(r)
    total_pnl = total_market - total_cost
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0.0
    total_realized = pm.get_total_realized_pnl()

    col1, col2, col3, col4 = st.columns(4)
    pnl_color = "#48bb78" if total_pnl >= 0 else "#fc8181"
    for col, header, val in [
        (col1, "💰 總成本", f"${total_cost:,.2f}"),
        (col2, "📊 總市值", f"${total_market:,.2f}"),
        (col3, "📈 未實現損益",
         f"{'▲' if total_pnl>=0 else '▼'} ${abs(total_pnl):,.2f} ({abs(total_pnl_pct):.2f}%)"),
        (col4, "✅ 已實現損益", f"${total_realized:+,.2f}"),
    ]:
        v_color = pnl_color if "損益" in header else "#e2e8f0"
        with col:
            st.markdown(
                f'<div class="card"><div class="card-header">{header}</div>'
                f'<div class="card-value" style="color:{v_color}">{val}</div></div>',
                unsafe_allow_html=True,
            )

    if rows:
        df = pd.DataFrame(rows)
        best = df.loc[df["unrealized_pnl_pct"].idxmax()]
        worst = df.loc[df["unrealized_pnl_pct"].idxmin()]
        cb, cw = st.columns(2)
        with cb:
            st.markdown(
                f'<div class="card" style="border-left:3px solid #48bb78;">'
                f'<div class="card-header">🏆 最佳</div>'
                f'<div style="font-size:16px;font-weight:600;color:#48bb78;">{best["ticker"]}</div>'
                f'<div style="font-size:13px;color:#a0aec0;">P&L: <b>+{best["unrealized_pnl_pct"]:.2f}%</b>'
                f' (${best["unrealized_pnl"]:+,.2f})</div></div>',
                unsafe_allow_html=True,
            )
        with cw:
            st.markdown(
                f'<div class="card" style="border-left:3px solid #fc8181;">'
                f'<div class="card-header">📉 最差</div>'
                f'<div style="font-size:16px;font-weight:600;color:#fc8181;">{worst["ticker"]}</div>'
                f'<div style="font-size:13px;color:#a0aec0;">P&L: <b>{worst["unrealized_pnl_pct"]:+.2f}%</b>'
                f' (${worst["unrealized_pnl"]:+,.2f})</div></div>',
                unsafe_allow_html=True,
            )


def _render_open_positions(pm: PortfolioManager):
    st.markdown("### 📋 持倉明細")
    positions = pm.get_open_positions()
    if not positions:
        st.info("目前沒有持倉。點擊「新增持倉」開始記錄。")
        return
    subtabs = st.tabs([f"{p.ticker} [{p.id}]" for p in positions])
    for i, pos in enumerate(positions):
        with subtabs[i]:
            ci, ct = st.columns(2)
            r = pm.get_unrealized_pnl(pos.id)
            if r:
                pc = "#48bb78" if r["unrealized_pnl"] >= 0 else "#fc8181"
                with ci:
                    st.markdown(
                        f'<div class="card">'
                        f'<div class="card-header">{pos.ticker}</div>'
                        f'<div class="metric-pair"><span class="metric-label">持倉</span><span class="metric-val">{r["shares"]:.2f}</span></div>'
                        f'<div class="metric-pair"><span class="metric-label">均價</span><span class="metric-val">${r["avg_cost"]:.2f}</span></div>'
                        f'<div class="metric-pair"><span class="metric-label">現價</span><span class="metric-val">${r["current_price"]:.2f}</span></div>'
                        f'<div class="metric-pair"><span class="metric-label">市值</span><span class="metric-val">${r["market_value"]:,.2f}</span></div>'
                        f'<div class="metric-pair"><span class="metric-label">未實現</span><span class="metric-val" style="color:{pc}">'
                        f'{"▲" if r["unrealized_pnl"]>=0 else "▼"} ${abs(r["unrealized_pnl"]):,.2f} ({r["unrealized_pnl_pct"]:+.2f}%)</span></div>'
                        f'<div class="metric-pair"><span class="metric-label">已實現</span><span class="metric-val">${pm.get_realized_pnl(pos.id):,.2f}</span></div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
            with ct:
                if pos.trades:
                    td = [{"D": t.date, "T": "🟢買" if t.type=="buy" else "🔴賣", "S": t.shares,
                           "P": f"${t.price:.2f}", "V": f"${t.shares*t.price:,.0f}"} for t in pos.trades]
                    st.markdown("**交易**")
                    st.dataframe(pd.DataFrame(td), use_container_width=True, hide_index=True, height=min(250, 40+35*len(td)))
            st.markdown("---")
            ca1, ca2, ca3, ca4 = st.columns(4)
            with ca1:
                with st.popover("➕ 買入", use_container_width=True):
                    _render_buy_form(pm, pos.id)
            with ca2:
                with st.popover("➖ 賣出", use_container_width=True):
                    _render_sell_partial_form(pm, pos.id)
            with ca3:
                if st.button("🔴 平倉", use_container_width=True, key=f"c_{pos.id}"):
                    if pm.close_position(pos.id):
                        st.success(f"{pos.ticker} 已平倉"); st.rerun()
                    else:
                        st.error("平倉失敗")
            with ca4:
                if st.button("🗑️ 刪除", use_container_width=True, key=f"d_{pos.id}"):
                    if pm.remove_position(pos.id):
                        st.success(f"已刪除"); st.rerun()


def _render_closed_positions(pm: PortfolioManager):
    closed = pm.get_closed_positions()
    if not closed:
        return
    st.markdown("### 📦 已平倉")
    for pos in closed:
        with st.expander(f"{pos.ticker} — 損益: ${pm.get_realized_pnl(pos.id):+,.2f}"):
            if pos.trades:
                td = [{"D": t.date, "T": "🟢買" if t.type=="buy" else "🔴賣", "S": t.shares,
                       "P": f"${t.price:.2f}"} for t in pos.trades]
                st.dataframe(pd.DataFrame(td), use_container_width=True, hide_index=True)
            if st.button("🗑️ 刪除", key=f"dc_{pos.id}"):
                pm.remove_position(pos.id); st.rerun()


def _render_add_position_form(pm: PortfolioManager):
    t = st.text_input("代碼", placeholder="AAPL", key="ap_t").strip().upper()
    c1, c2 = st.columns(2)
    with c1:
        s = st.number_input("股數", min_value=0.0, step=1.0, key="ap_s")
    with c2:
        p = st.number_input("價格$", min_value=0.0, step=0.01, format="%.2f", key="ap_p")
    d = st.date_input("日期", value="today", key="ap_d")
    if st.button("✅ 確認", use_container_width=True, key="ap_b"):
        if not t or s<=0 or p<=0:
            st.error("請填完整"); return
        pm.add_position(t, trades=[Trade(date=d.strftime("%Y-%m-%d"), type="buy", shares=s, price=p)])
        st.success(f"{t} 已建立"); st.rerun()


def _render_buy_form(pm: PortfolioManager, pid: str):
    c1, c2 = st.columns(2)
    with c1:
        s = st.number_input("股數", min_value=0.0, step=1.0, key=f"b_s_{pid}")
    with c2:
        p = st.number_input("價格$", min_value=0.0, step=0.01, format="%.2f", key=f"b_p_{pid}")
    d = st.date_input("日期", value="today", key=f"b_d_{pid}")
    if st.button("✅ 確認", use_container_width=True, key=f"b_b_{pid}"):
        if s<=0 or p<=0: return
        pm.add_trade(pid, Trade(date=d.strftime("%Y-%m-%d"), type="buy", shares=s, price=p))
        st.success("已新增"); st.rerun()


def _render_sell_partial_form(pm: PortfolioManager, pid: str):
    pos = pm.get_position(pid)
    if not pos: return
    c1, c2 = st.columns(2)
    with c1:
        s = st.number_input("股數", min_value=0.0, max_value=pos.net_shares, step=1.0, key=f"ss_{pid}")
    with c2:
        p = st.number_input("價格$", min_value=0.0, step=0.01, format="%.2f", key=f"sp_{pid}")
    d = st.date_input("日期", value="today", key=f"sd_{pid}")
    if st.button("✅ 確認", use_container_width=True, key=f"sb_{pid}"):
        if s<=0 or p<=0: return
        pm.add_trade(pid, Trade(date=d.strftime("%Y-%m-%d"), type="sell", shares=s, price=p))
        st.success(f"賣出 {s} 股"); st.rerun()


def _render_date_range_form(pm: PortfolioManager):
    c1, c2 = st.columns(2)
    with c1:
        sd = st.date_input("開始", value=None, key="dr_s")
    with c2:
        ed = st.date_input("結束", value=None, key="dr_e")
    if st.button("📊 計算", use_container_width=True, key="dr_b"):
        if not sd or not ed: st.error("請選日期"); return
        if sd>=ed: st.error("結束須晚於開始"); return
        rs = pm.get_date_range_return(sd.strftime("%Y-%m-%d"), ed.strftime("%Y-%m-%d"))
        if not rs: st.info("無資料"); return
        df = pd.DataFrame(rs)
        st.dataframe(df.rename(columns={"ticker":"股票","start_price":"開始","end_price":"結束",
                                        "price_return_pct":"回報%","pnl_dollars":"盈虧$"}),
                      use_container_width=True, hide_index=True)
        tp = df["pnl_dollars"].sum()
        ar = df["price_return_pct"].mean()
        st.markdown(f'<div class="card"><div class="card-header">📈 匯總</div>'
                    f'<div>平均: <b>{ar:+.2f}%</b> | 總盈虧: <b style="color:{"#48bb78" if tp>=0 else "#fc8181"}">${tp:+,.2f}</b></div></div>',
                    unsafe_allow_html=True)

# ── 持倉管理（當選擇持倉管理頁面時）──
if page == "portfolio":
    _render_portfolio_tab()
    st.caption(f"最後更新: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    st.stop()

# ── 動態止損監控（當選擇止損監控頁面時）──
if page == "stops":
    render_dynamic_stops_tab()
    st.caption(f"最後更新: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    st.stop()

# ── 實時報價 + 財務數據 + 新聞（全部來自 DB 緩存）──
data = load_stock_data(selected_ticker, days=days_range, force_refresh=False)
prices = data["prices"]
financials = data["financials"]
news_items = data["news"]

# ── 市場診斷提示（協助確認假期過濾是否生效）──
# 返回信號首頁按鈕（當從信號卡片跳轉時顯示）
from_home = st.session_state.get("_came_from_home", False)
col_b, col_spacer = st.columns([1, 5])
with col_b:
    if st.button("← 返回信號首頁", use_container_width=True):
        st.session_state.pop("navigate_to_ticker", None)
        st.session_state["_came_from_home"] = False
        st.rerun()

_mkt = get_market_from_ticker(selected_ticker)
if _mkt == "HK":
    _sample_hol = list(HKEX_HOLIDAYS)[:3]
    _hol_label = f"香港 [HKEX] | 假期例：{_sample_hol}"
else:
    _sample_hol = list(US_HOLIDAYS)[:3]
    _hol_label = f"美國 [NYSE/NASDAQ] | 假期例：{_sample_hol}"
st.caption(f"🔍 市場：{_hol_label} | 過濾模式：is_market_trading_day")

# 從股價歷史重構 DataFrame（給圖表用）
if prices:
    df_prices = pd.DataFrame(prices)
    if "trade_date" in df_prices.columns:
        df_prices["Date"] = pd.to_datetime(df_prices["trade_date"])
        df_prices.set_index("Date", inplace=True)
        df_prices.rename(columns={
            "open": "open", "high": "high", "low": "low",
            "close": "close", "volume": "volume"
        }, inplace=True)
    # 確保價格按日期排序（DuckDB 數據是 newest-first，rolling() 需要 oldest-first）
    df_prices = df_prices.sort_index()
    # 計算技術指標
    from indicator_calculator import calculate_all_indicators
    df_prices = calculate_all_indicators(df_prices)
    latest_price = prices[0]["close"] if prices else None
    prev_close = prices[1]["close"] if len(prices) > 1 else None
else:
    df_prices = pd.DataFrame()
    latest_price = None
    prev_close = None

change = (latest_price - prev_close) if latest_price and prev_close else None
change_pct = (change / prev_close * 100) if change and prev_close else None
vol = prices[0]["volume"] if prices else None

# 公司名稱
company_name = financials.get("company_name", selected_ticker) if financials else selected_ticker

# ── ① Header：報價卡片 ──
st.markdown(f"## 📈 {company_name} ({selected_ticker})")
def _fmt_abs(v):  return f"{'+' if v >= 0 else ''}{v:,.2f}" if v is not None else None
def _fmt_pct(v):  return f"{'+' if v >= 0 else ''}{v:.2f}%" if v is not None else None

r1, r2, r3, r4 = st.columns(4)
price_val = f"${latest_price:.2f}" if latest_price else "—"
chg_abs = change if change else None
chg_pct = change_pct if change_pct else None
vol_val = f"{vol:,.0f}" if vol else "—"

card(r1, "最新價格", price_val)
card(r2, "漲跌額",    _fmt_abs(chg_abs), chg_abs)
card(r3, "漲跌幅",   _fmt_pct(chg_pct), chg_pct)
card(r4, "成交量",   vol_val)

st.markdown("---")

# ── ② 主體區域 ──
col_chart, col_side = st.columns([3, 2])

# ─── 左：圖表 ───
with col_chart:
    if not df_prices.empty:
        cutoff = pd.Timestamp.now() - pd.Timedelta(days=days_range)
        df_plot = df_prices[df_prices.index >= cutoff]
        # ★ 確保排序一致：yfinance 不保證返回有序數據，必須先 sort
        df_plot = df_plot.sort_index()
        # ★ 先 dropna 再過濾非交易日，確保形態檢測和圖表使用完全一致的乾淨數據
        df_plot = df_plot.dropna(subset=["open", "high", "low", "close"])
        df_plot = filter_trading_days(df_plot, selected_ticker)
        # 形態識別（df_plot 已完全乾淨，形態索引和 dropna+reset_index 後的圖表完全對齊）
        # lookback 設為 days_range（無上限 cap），確保整個可視範圍內的形態都能被檢測
        patterns = get_latest_patterns(df_plot, lookback=days_range)
        # 詳細日誌：寫到 stderr（Railway 一定捕獲）
        import sys
        all_rows = df_plot.sort_index()  # 确保日志顺序与实际一致
        msg = "[DEBUG] " + selected_ticker + " " + str(days_range) + "d total=" + str(len(all_rows)) + " rows\n"
        for i, row in all_rows.iterrows():
            body = abs(float(row['close']) - float(row['open']))
            rng = float(row['high']) - float(row['low'])
            ratio = body / rng if rng else 0
            msg += "[DEBUG]   " + str(i.date()) + " O=" + str(round(float(row['open']),2)) + " C=" + str(round(float(row['close']),2)) + " H=" + str(round(float(row['high']),2)) + " L=" + str(round(float(row['low']),2)) + " body/r=" + str(round(ratio,3)) + "\n"
        msg += "[DEBUG] Patterns: " + str([(p.name, p.metadata.get("idx"), p.indices) for p in patterns]) + "\n"
        sys.stderr.write(msg)
        tab_bb, tab_rsi, tab_macd, tab_kdj, tab_line = st.tabs(
            ["📊 蠟燭圖+BB", "📉 RSI", "📊 MACD", "📊 KDJ", "📈 折線圖"])
        with tab_bb:
            fig_candle = plot_candlestick(df_plot, selected_ticker, company_name, patterns, show_bb=True)
            st.plotly_chart(fig_candle, width="stretch")
        with tab_rsi:
            st.plotly_chart(plot_rsi(df_plot, selected_ticker), width="stretch")
        with tab_macd:
            st.plotly_chart(plot_macd(df_plot, selected_ticker), width="stretch")
        with tab_kdj:
            st.plotly_chart(plot_kdj(df_plot, selected_ticker), width="stretch")
        with tab_line:
            st.plotly_chart(plot_line(df_plot, selected_ticker, company_name), width="stretch")
        st.plotly_chart(plot_volume(df_plot, selected_ticker), width="stretch")

        # ── 💎 價值投資分析（移至成交量後，全寬顯示）──
        try:
            info_raw = get_stock_info(selected_ticker)

            def _v(key, pct=False, mult=1):
                v = info_raw.get(key)
                if v is None:
                    return None
                try:
                    return round(float(v) * mult, 2) if pct else round(float(v) * mult, 4)
                except Exception:
                    return None

            # 核心數據
            pe_t = _v("trailingPE")
            pe_f = _v("forwardPE")
            pb = _v("priceToBook")
            peg = _v("trailingPegRatio")
            de_yield = _v("dividendYield", pct=True)
            earn_gr = _v("earningsGrowth", pct=True)
            op_margin = _v("operatingMargins", pct=True)
            gross_margin = _v("grossMargins", pct=True)
            market_cap = _v("marketCap")
            revenue = _v("totalRevenue")
            target = _v("targetMeanPrice")
            target_high = _v("targetHighPrice")
            target_low = _v("targetLowPrice")
            current_price = float(latest_price) if latest_price is not None else None

            # 目標價空間
            target_upside = None
            if target is not None and current_price is not None:
                try:
                    target_upside = (float(target) - float(current_price)) / float(current_price)
                except Exception:
                    target_upside = None

            # ── 10 分制評分 ──
            scores = []
            details = []

            # 1. P/E 估值 (max 2 pts)
            if pe_t and pe_t > 0:
                if pe_t < 12:
                    scores.append(2.0)
                    details.append(f"✅ 本益比 {pe_t:.1f}x — 顯著低於同業 ✓")
                elif pe_t < 20:
                    scores.append(1.5)
                    details.append(f"📐 本益比 {pe_t:.1f}x — 合理偏低")
                elif pe_t < 30:
                    scores.append(1.0)
                    details.append(f"📐 本益比 {pe_t:.1f}x — 合理偏高")
                elif pe_t < 40:
                    scores.append(0.5)
                    details.append(f"⚠️ 本益比 {pe_t:.1f}x — 偏高")
                else:
                    scores.append(0)
                    details.append(f"🔴 本益比 {pe_t:.1f}x — 極高")
            else:
                details.append("❓ 本益比 — 數據不足")

            # 2. Forward PE vs Trailing (max 1 pt)
            if pe_t and pe_f and pe_t > 0 and pe_f > 0:
                if pe_f < pe_t * 0.85:
                    scores.append(1.0)
                    details.append(f"✅ Forward PE ({pe_f:.1f}x) << Trailing PE ({pe_t:.1f}x) — 盈利大幅改善 ✓")
                elif pe_f < pe_t:
                    scores.append(0.5)
                    details.append(f"📐 Forward PE ({pe_f:.1f}x) < Trailing PE — 盈利改善中")
                else:
                    scores.append(0)
                    details.append(f"📐 Forward PE ({pe_f:.1f}x) ≥ Trailing PE — 盈利預期持平或下滑")
            else:
                details.append("❓ Forward PE — 數據不足")

            # 3. 目標價空間 (max 1.5 pts)
            if target_upside is not None:
                if target_upside > 0.30:
                    scores.append(1.5)
                    details.append(f"🎯 目標價 ${target:.2f} — 潛在升幅 {target_upside:.1%} 空間巨大 ✓")
                elif target_upside > 0.15:
                    scores.append(1.0)
                    details.append(f"🎯 目標價 ${target:.2f} — 潛在升幅 {target_upside:.1%} ✓")
                elif target_upside > 0:
                    scores.append(0.5)
                    details.append(f"🎯 目標價 ${target:.2f} — 潛在升幅 {target_upside:.1%}")
                else:
                    scores.append(0)
                    details.append(f"🎯 目標價 ${target:.2f} — 低於現價，分析師偏淡")
            else:
                details.append("❓ 目標價 — 數據不足")

            # 4. PEG (max 1 pt)
            if peg and peg > 0:
                if peg < 0.8:
                    scores.append(1.0)
                    details.append(f"✅ PEG {peg:.2f}x — 低於 0.8x，成長價值極佳 ✓")
                elif peg < 1.5:
                    scores.append(0.5)
                    details.append(f"📐 PEG {peg:.2f}x — 合理")
                else:
                    scores.append(0)
                    details.append(f"⚠️ PEG {peg:.2f}x — 偏高")
            else:
                details.append("❓ PEG — 數據不足")

            # 5. 盈利增長 (max 1.5 pts)
            if earn_gr is not None:
                if earn_gr > 0.15:
                    scores.append(1.5)
                    details.append(f"✅ 盈利增長 {earn_gr:.1%} — 雙位數增長 ✓")
                elif earn_gr > 0.08:
                    scores.append(1.0)
                    details.append(f"📐 盈利增長 {earn_gr:.1%} — 穩定增長")
                elif earn_gr > 0:
                    scores.append(0.5)
                    details.append(f"📐 盈利增長 {earn_gr:.1%} — 緩慢")
                else:
                    scores.append(0)
                    details.append(f"🔴 盈利 {earn_gr:.1%} — 衰退")
            else:
                details.append("❓ 盈利增長 — 數據不足")

            # 6. 股息 (max 0.5 pt)
            if de_yield is not None:
                if de_yield > 0.03:
                    scores.append(0.5)
                    details.append(f"💵 股息率 {de_yield:.2%} — 高收益 ✓")
                elif de_yield > 0.01:
                    scores.append(0.25)
                    details.append(f"💵 股息率 {de_yield:.2%}")
                else:
                    details.append(f"💵 股息率 {de_yield:.2%} — 偏低")
            else:
                details.append("❌ 無股息")

            # 7. 護城河 (max 1.5 pts) — 毛利率 + 營業利潤率 + 市值規模
            moat_score = 0.0
            moat_parts = []
            if gross_margin is not None:
                if gross_margin > 0.50:
                    moat_score += 0.5
                    moat_parts.append(f"毛利率 {gross_margin:.1%} ✓")
                elif gross_margin > 0.30:
                    moat_score += 0.25
                    moat_parts.append(f"毛利率 {gross_margin:.1%}")
                else:
                    moat_parts.append(f"毛利率 {gross_margin:.1%} — 偏低")
            else:
                moat_parts.append("毛利率 — N/A")
            if op_margin is not None:
                if op_margin > 0.25:
                    moat_score += 0.5
                    moat_parts.append(f"營利率 {op_margin:.1%} ✓")
                elif op_margin > 0.15:
                    moat_score += 0.25
                    moat_parts.append(f"營利率 {op_margin:.1%}")
                else:
                    moat_parts.append(f"營利率 {op_margin:.1%} — 競爭激烈")
            else:
                moat_parts.append("營利率 — N/A")
            if market_cap is not None:
                if market_cap > 200e9:
                    moat_score += 0.5
                    moat_parts.append(f"市值 ${market_cap/1e9:.1f}B — 巨無霸 ✓")
                elif market_cap > 50e9:
                    moat_score += 0.25
                    moat_parts.append(f"市值 ${market_cap/1e9:.1f}B — 大型")
                else:
                    moat_parts.append(f"市值 ${market_cap/1e9:.1f}B — 中小型")
            else:
                moat_parts.append("市值 — N/A")
            scores.append(moat_score)
            details.append(f"🏰 護城河: {' | '.join(moat_parts)}（{moat_score:.2f}/1.5）")

            # ── 總分 ──
            total_score = sum(scores)
            if total_score >= 8.0:
                badge = "🟢 強烈建議買入"
                badge_color = "#48bb78"
            elif total_score >= 6.0:
                badge = "🟢 值得持有"
                badge_color = "#48bb78"
            elif total_score >= 4.5:
                badge = "🟡 謹慎關注"
                badge_color = "#ecc94b"
            elif total_score >= 3.0:
                badge = "🟠 需進一步觀察"
                badge_color = "#ed8936"
            else:
                badge = "🔴 不建議現價買入"
                badge_color = "#fc8181"

            html = f"""
            <div class="card">
                <div class="card-header">💎 價值投資評分 — <span style="color:{badge_color};font-weight:700;">{badge}</span>（{total_score:.1f}/10 分）</div>
                <div style="margin-top: 8px;">
            """
            for d in details:
                html += f'<div style="font-size: 13px; color: #cbd5e0; padding: 3px 0;">{d}</div>'
            html += '</div></div>'
            st.markdown(html, unsafe_allow_html=True)

        except Exception as e:
            st.warning(f"⚠️ 價值投資分析暫不可用：{e}")

    else:
        st.error(f"無法獲取 {selected_ticker} 的股票數據")

# ─── 右：財務指標 + 新聞 ──
with col_side:
    info_raw = get_stock_info(selected_ticker)

    def _f(key, pct=False, mult=1):
        v = info_raw.get(key)
        if v is None:
            return None
        try:
            return round(float(v) * mult, 2) if pct else round(float(v) * mult, 4)
        except Exception:
            return None

    info = {
        "trailing_pe":      _f("trailingPE"),
        "forward_pe":       _f("forwardPE"),
        "price_to_book":    _f("priceToBook"),
        "peg_ratio":        _f("trailingPegRatio"),
        "gross_profit":     _f("grossProfits"),
        "total_revenue":    _f("totalRevenue"),
        "net_income":       _f("netIncomeToCommon"),
        "operating_margin": _f("operatingMargins", pct=True),
        "profit_margin":    _f("profitMargins", pct=True),
        "revenue_growth":   _f("revenueGrowth", pct=True),
        "earnings_growth":  _f("earningsGrowth", pct=True),
        "forward_eps":      _f("forwardEps"),
        "dividend_yield":   _f("dividendYield", pct=True),
        "recommendation":   info_raw.get("recommendationKey"),
        "analyst_count":    info_raw.get("numberOfAnalystOpinions"),
        "52w_high":         info_raw.get("fiftyTwoWeekHigh"),
        "52w_low":          info_raw.get("fiftyTwoWeekLow"),
        "target_mean_price": info_raw.get("targetMeanPrice"),
    }

    def fp(v): return f"{v:.2f}" if v is not None else "—"
    def pp(v): return f"{v:.2f}%" if v is not None else "—"
    def fm(v):  # format absolute money (billions/millions)
        if v is None: return "—"
        if abs(v) >= 1e9: return f"{v/1e9:.2f}B"
        if abs(v) >= 1e6: return f"{v/1e6:.2f}M"
        if abs(v) >= 1e3: return f"{v/1e3:.2f}K"
        return f"{v:.2f}"

    # 估值 card
    section_card("📊 估值", metric_row("P/E (Trailing)", fp(info["trailing_pe"])) +
                              metric_row("P/E (Forward)", fp(info["forward_pe"])) +
                              metric_row("P/B", fp(info["price_to_book"])) +
                              metric_row("PEG", fp(info["peg_ratio"])))

    # 盈利能力 card
    section_card("💰 盈利能力", metric_row("Gross Profit", fm(info["gross_profit"])) +
                                  metric_row("Net Income", fm(info["net_income"])) +
                                  metric_row("Total Revenue", fm(info["total_revenue"])) +
                                  metric_row("Op. Margin", pp(info["operating_margin"])) +
                                  metric_row("Profit Margin", pp(info["profit_margin"])))

    # 增長 & 股息 card
    section_card("📈 增長 & 股息", metric_row("Revenue Growth", pp(info["revenue_growth"])) +
                                      metric_row("Earnings Growth", pp(info["earnings_growth"])) +
                                      metric_row("Forward EPS", fp(info["forward_eps"])) +
                                      metric_row("Dividend Yield", pp(info["dividend_yield"])))

    # 分析師觀點 card
    rec = info.get("recommendation")
    count = info.get("analyst_count")
    high = info.get("52w_high")
    low = info.get("52w_low")
    target = info.get("target_mean_price")
    analyst_html = metric_row("評級", rec.title() if rec else "—") + \
                   metric_row("分析師數量", str(count) if count else "—") + \
                   metric_row("52W High", f"${fp(high)}" if high else "—") + \
                   metric_row("52W Low", f"${fp(low)}" if low else "—") + \
                   metric_row("目標價", f"${fp(target)}" if target else "—")
    section_card("🎯 分析師觀點", analyst_html)

    st.markdown("---")

    # ── ④ 最新消息（橫跨全寬）—— 最多 7 天，分頁顯示 ──
    st.markdown("### 📰 最新消息")

# Session state for pagination
INITIAL_NEWS = 10
MORE_NEWS = 10
if "news_count" not in st.session_state:
    st.session_state.news_count = INITIAL_NEWS

# ─────────────────────────────────────────────────────────
# NEWS SECTION
# ─────────────────────────────────────────────────────────

# Filter to last 7 days
cutoff_7d = pd.Timestamp.now() - pd.Timedelta(days=7)
news = [n for n in news_items if pd.to_datetime(n["pub_date"], errors="coerce") >= cutoff_7d]

if news:
    total = len(news)
    shown = st.session_state.news_count

    # 2-column news layout
    n_cols = st.columns(2)
    for i, item in enumerate(news[:shown]):
        col = n_cols[i % 2]
        with col:
            st.markdown(news_card_html(item["title"], item["source"], item["pub_date"], item["link"]), unsafe_allow_html=True)

    # More button
    if shown < total:
        st.button(f"🔽 更多消息 （已顯示 {shown}/{total}）", on_click=lambda: st.session_state.update(news_count=st.session_state.news_count + MORE_NEWS))
else:
    st.info("暫無最新消息")

st.caption(f"最後更新: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | 股票代碼: {selected_ticker}")


# ═══════════════════════════════════════════════════════════════════════
#  🗂️ 持倉管理 UI 函數（內聯，無需外部文件）
# ═══════════════════════════════════════════════════════════════════════
