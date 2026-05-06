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

st.set_page_config(page_title="Stock Dashboard", page_icon="📈", layout="wide")

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


# ── Prediction box CSS ──────────────────────────────────────────────
st.markdown("""
<style>
.prediction-box {
    border-radius: 12px;
    padding: 16px 20px;
    margin-bottom: 12px;
    font-family: -apple-system, BlinkMacSystemFont, sans-serif;
}
.prediction-box.bullish { background: linear-gradient(135deg, #0a2e1a, #0d3d22); border: 1px solid #00FF7F; }
.prediction-box.bearish { background: linear-gradient(135deg, #2e0a0a, #3d0d0d); border: 1px solid #FF4444; }
.prediction-box.neutral { background: linear-gradient(135deg, #1a1a0a, #2d2d0d); border: 1px solid #FFD700; }
.pred-header { font-size: 16px; font-weight: 700; margin-bottom: 8px; color: #e2e8f0; }
.pred-meta { display: flex; gap: 20px; margin-bottom: 10px; font-size: 13px; color: #a0aec0; }
.pred-meta b { color: #e2e8f0; }
.pred-details, .pred-warnings { margin: 0; padding-left: 18px; font-size: 12px; color: #a0aec0; line-height: 1.8; }
.pred-warnings { color: #f39c12; }
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
from predictor import predict, format_prediction_html


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


def plot_candlestick(df, ticker, company_name="", patterns=None):
    """Candlestick chart: integer x-axis (no gaps) with date labels + pattern markers."""
    if patterns is None:
        patterns = []
    df = df.dropna(subset=["open", "high", "low", "close"]).copy()
    df = df.sort_index()
    # 保存日期用於 hover 和 x 軸標籤（reset_index 前做）
    dates = df.index.tolist()
    df = df.reset_index(drop=True)   # ← 整數索引，消滅所有空隙
    title = f"{ticker}" + (f" — {company_name}" if company_name else "")

    fig = go.Figure(data=[go.Candlestick(
        x=list(range(len(df))),       # ← 整數坐標，點與點之間等距
        open=df["open"], high=df["high"], low=df["low"], close=df["close"],
        name="OHLC", xhoverformat="%Y-%m-%d",
        text=dates, hovertemplate='%{text}<br>O: %{customdata[0]:.2f} H: %{customdata[1]:.2f} L: %{customdata[2]:.2f} C: %{customdata[3]:.2f}<extra></extra>',
        customdata=df[["open","high","low","close"]].values,
    )])
    xaxis_cfg = _make_date_axis_config(dates)
    xaxis_cfg['rangeslider']['visible'] = False
    fig.update_layout(
        title=title, yaxis_title="HKD / USD",
        xaxis_title="日期", template="plotly_dark",
        height=460, xaxis=xaxis_cfg,
    )
    # 疊加形態標記
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

# ── 側邊欄：股票搜尋 ──
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
    # 用 selectbox 顯示建議讓用戶選擇
    options = [format_suggestion(q) for q in suggestions]
    selected = st.sidebar.selectbox(
        "選擇股票",
        options=options,
        label_visibility="collapsed"
    )
    # 從選擇回推 symbol
    idx = options.index(selected)
    selected_ticker = suggestions[idx]["symbol"]
elif search_query:
    # 有輸入但沒有建議，嘗試直接使用輸入作為 ticker
    st.sidebar.warning(f"找不到「{search_query}」，請嘗試其他關鍵字")

# 如果沒有輸入，使用預設
if not selected_ticker:
    st.sidebar.caption("直接輸入股票代碼，例如：3968.HK、0700.HK、TSLA、JPM")
    # 嘗試直接解析輸入作為 ticker
    raw = search_query.strip().upper()
    if raw:
        # 如果看起來像 ticker（包含 . 或全大寫字母）
        if "." in raw or raw.isalpha():
            selected_ticker = raw
    if not selected_ticker:
        selected_ticker = "3968.HK"  # 預設

days_range = st.sidebar.slider("顯示天數", 30, 365, 90)

# ── 實時報價 + 財務數據 + 新聞（全部來自 DB 緩存）──
data = load_stock_data(selected_ticker, force_refresh=False)
prices = data["prices"]
financials = data["financials"]
news_items = data["news"]

# ── 市場診斷提示（協助確認假期過濾是否生效）──
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
        # 形態識別
        patterns = get_latest_patterns(df_plot, lookback=min(days_range, 60))
        # 詳細日誌：寫到 stderr（Railway 一定捕獲）
        import sys
        all_rows = df_plot
        msg = "[DEBUG] " + selected_ticker + " " + str(days_range) + "d total=" + str(len(all_rows)) + " rows\n"
        for i, row in all_rows.iterrows():
            body = abs(float(row['close']) - float(row['open']))
            rng = float(row['high']) - float(row['low'])
            ratio = body / rng if rng else 0
            msg += "[DEBUG]   " + str(i.date()) + " O=" + str(round(float(row['open']),2)) + " C=" + str(round(float(row['close']),2)) + " H=" + str(round(float(row['high']),2)) + " L=" + str(round(float(row['low']),2)) + " body/r=" + str(round(ratio,3)) + "\n"
        msg += "[DEBUG] Patterns: " + str([(p.name, p.metadata.get("idx"), p.indices) for p in patterns]) + "\n"
        sys.stderr.write(msg)
        t1, t2 = st.tabs(["📊 蠟燭圖", "📈 折線圖"])
        with t1:
            fig_candle = plot_candlestick(df_plot, selected_ticker, company_name, patterns)
            st.plotly_chart(fig_candle, width="stretch")
        with t2:
            st.plotly_chart(plot_line(df_plot, selected_ticker, company_name), width="stretch")
        st.plotly_chart(plot_volume(df_plot, selected_ticker), width="stretch")

        # 形態圖例
        legend = build_pattern_legend(patterns)
        if patterns:
            st.markdown(f"**📐 形態標記說明**\n\n{legend}", unsafe_allow_html=True)

        # 走勢預判
        pred = predict(df_plot)
        st.markdown("### 🎯 走勢預判")
        st.markdown(format_prediction_html(pred), unsafe_allow_html=True)
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

# ── ③ 最新消息（橫跨全寬）—— 最多 7 天，分頁顯示 ──
st.markdown("### 📰 最新消息")

# Session state for pagination
INITIAL_NEWS = 10
MORE_NEWS = 10
if "news_count" not in st.session_state:
    st.session_state.news_count = INITIAL_NEWS

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
