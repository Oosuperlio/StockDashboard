"""
Load daily signal results saved by signal_scanner.py --save
for consumption by the Railway Streamlit Dashboard.

Files are stored in data/signals/:
  - latest_signals_us.csv    (US stocks — symbols NOT ending in .HK)
  - latest_signals_hk.csv    (HK stocks — symbols ending in .HK)
  - latest_signals_all.csv   (combined)

These files are overwritten each time signal_scanner.py runs with --save.
"""

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

# ─── Path resolution ────────────────────────────────────────────────────────

SIGNALS_DIR = Path(__file__).resolve().parent.parent / "data" / "signals"

# Written by scripts/daily_downloader.py at the end of every price-data update.
DATA_UPDATE_REPORT = SIGNALS_DIR.parent / "last_update_report.txt"

_SIGNAL_FILES = {
    "us": "latest_signals_us.csv",
    "hk": "latest_signals_hk.csv",
    None: "latest_signals_all.csv",
}

# ─── Market sessions ────────────────────────────────────────────────────────
# 交易所當地收盤時間（HKEX / NYSE 均為 16:00）。加 15 分鐘緩衝，
# 避免「剛收盤但資料尚未寫入」被誤判為延遲。
_CLOSE_HOUR = 16
_CLOSE_BUFFER_MIN = 15

# (IANA 時區, UTC 固定偏移 fallback — 僅在缺少 tzdata 時使用)
_MARKET_TZ = {"us": ("America/New_York", -5), "hk": ("Asia/Hong_Kong", 8)}

# 各市場每日信號掃描時間（交易所當地時間，開市前）：
#   HK  cron `10 18 * * *` PT → 09:10 HKT
#   US  cron `10 6 * * *`  PT → 09:10 ET
# 掃描在開市前執行 ⇒ 產生的信號落在「上一次收盤」那一天。
_SCAN_TIME = {"us": (9, 10), "hk": (9, 10)}

_HOLIDAY_CACHE: dict = {}


def load_daily_signals(
    market: Optional[str] = None,
    sort_by: tuple = ("tier", "win_rate"),
) -> pd.DataFrame:
    """
    Load the latest signal results.

    Parameters
    ----------
    market : str or None
        'us' — US stocks only (symbols without .HK suffix)
        'hk' — HK stocks only (symbols with .HK suffix)
        None — all signals combined

    sort_by : tuple
        Column(s) to sort by. Default: tier ascending, win_rate descending.

    Returns
    -------
    pd.DataFrame with columns:
        symbol, sector, subsector, indicator, signal, pattern,
        pattern_conf, confidence, win_rate, win_rate_stock, win_rate_sector,
        stock_n, sector_n, avg_return, price, tp1_price, tp2_price, sl_price,
        date, tier, volume_confirmed
    """
    fname = _SIGNAL_FILES.get(market, "latest_signals_all.csv")

    path = SIGNALS_DIR / fname
    if not path.exists():
        return pd.DataFrame()

    try:
        df = pd.read_csv(path)
    except Exception:
        return pd.DataFrame()

    if df.empty:
        return df

    # Ensure tier is integer for proper sorting
    if "tier" in df.columns:
        df["tier"] = df["tier"].astype(int)

    # Sort: tier ascending, win_rate descending
    sort_cols = []
    ascending = []
    for col in sort_by:
        if col in df.columns:
            sort_cols.append(col)
            ascending.append(False if col in ("win_rate", "win_rate_stock", "win_rate_sector", "confidence") else True)

    if sort_cols:
        df = df.sort_values(sort_cols, ascending=ascending)

    return df.reset_index(drop=True)


def get_signal_summary(market: Optional[str] = None) -> dict:
    """
    Return a lightweight summary (tier counts) without loading the full DataFrame.
    """
    df = load_daily_signals(market)
    if df.empty:
        return {"total": 0, "tier_1": 0, "tier_2": 0, "tier_3": 0}

    return {
        "total": len(df),
        "tier_1": int((df["tier"] == 1).sum()),
        "tier_2": int((df["tier"] == 2).sum()),
        "tier_3": int((df["tier"] == 3).sum()),
    }


# ─── Dates & data freshness ─────────────────────────────────────────────────


def _signal_path(market: Optional[str] = None) -> Path:
    """Path of the signal CSV for a market ('us' / 'hk' / None = combined)."""
    return SIGNALS_DIR / _SIGNAL_FILES.get(market, "latest_signals_all.csv")


def _file_mtime(path: Path) -> Optional[datetime]:
    """File mtime as a tz-aware UTC datetime, or None."""
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None


def signal_date(market: Optional[str] = None) -> Optional[str]:
    """
    Return the latest **signal trading day** — the max value of the CSV
    ``date`` column — as ``YYYY-MM-DD``, or None if no signals found.

    ⚠️ Never derive this from the file mtime: inside the container the mtime is
    the Docker *build* time (UTC), which turns a one-day-old dataset into a
    "today" label and masks data delay (2026-10-27 incident: header said
    2026-10-27 while the rendered data was 2026-10-26). mtime is used only as a
    last-resort fallback when the ``date`` column is missing or unparseable.

    Parameters
    ----------
    market : str or None
        'us' / 'hk' to read that market's CSV, None for the combined file.
    """
    path = _signal_path(market)
    if not path.exists():
        return None

    # Primary source: max of the `date` column (signal trading day).
    try:
        dates = pd.to_datetime(
            pd.read_csv(path, usecols=["date"])["date"], errors="coerce"
        ).dropna()
        if not dates.empty:
            return dates.max().strftime("%Y-%m-%d")
    except (ValueError, KeyError, pd.errors.EmptyDataError, pd.errors.ParserError):
        pass  # column missing / malformed CSV → fall through to mtime
    except Exception:
        pass

    # Last-resort fallback only.
    mtime = _file_mtime(path)
    return mtime.strftime("%Y-%m-%d") if mtime else None


def data_update_time() -> Optional[datetime]:
    """
    Return the data-update / deployment timestamp as a tz-aware UTC datetime.

    Sources, most specific first:
      1. ``data/last_update_report.txt`` mtime — written by
         ``scripts/daily_downloader.py`` at the end of every price update.
      2. newest mtime among ``data/signals/*.csv`` — locally this is the signal
         scan time; inside the container it is the Docker **build** time, which
         is exactly what we want to show as "部署時間".

    Returns None when nothing is readable.
    """
    candidates = [
        _file_mtime(DATA_UPDATE_REPORT),
        _file_mtime(SIGNALS_DIR / "latest_signals_all.csv"),
        _file_mtime(SIGNALS_DIR / "latest_signals_us.csv"),
        _file_mtime(SIGNALS_DIR / "latest_signals_hk.csv"),
    ]
    present = [c for c in candidates if c is not None]
    return max(present) if present else None


def data_update_time_str(utc_offset_hours: int = 8, tz_label: str = "HKT") -> Optional[str]:
    """
    Human-readable data-update timestamp, e.g. ``2026-10-27 21:33 HKT``.

    Defaults to HKT (UTC+8) — the timezone Edward works in.
    """
    ts = data_update_time()
    if ts is None:
        return None
    local = ts.astimezone(timezone(timedelta(hours=utc_offset_hours)))
    return local.strftime("%Y-%m-%d %H:%M") + f" {tz_label}"


def _us_utc_offset_hours(d: date) -> int:
    """
    US Eastern UTC offset (-4 DST / -5 standard), used only when tzdata is
    unavailable. DST runs 2nd Sunday of March → 1st Sunday of November.
    """

    def nth_sunday(year: int, month: int, n: int) -> date:
        first = date(year, month, 1)
        first_sunday = first + timedelta(days=(6 - first.weekday()) % 7)
        return first_sunday + timedelta(weeks=n - 1)

    return -4 if nth_sunday(d.year, 3, 2) <= d < nth_sunday(d.year, 11, 1) else -5


def market_now(market: str, now: Optional[datetime] = None) -> datetime:
    """Current local (naive) time on the exchange of ``market`` ('us' / 'hk')."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    tz_name, fallback_offset = _MARKET_TZ.get(market, _MARKET_TZ["us"])
    try:
        from zoneinfo import ZoneInfo  # Python 3.9+

        return now.astimezone(ZoneInfo(tz_name)).replace(tzinfo=None)
    except Exception:  # missing tzdata (e.g. slim container images)
        offset = (
            _us_utc_offset_hours(now.date()) if market == "us" else fallback_offset
        )
        return now.astimezone(timezone(timedelta(hours=offset))).replace(tzinfo=None)


def _market_holidays(market: str):
    """Exchange holiday calendar for ``market`` (empty set if unavailable)."""
    if market in _HOLIDAY_CACHE:
        return _HOLIDAY_CACHE[market]
    cal = set()
    try:
        import holidays as _holidays

        if market == "hk":
            cal = _holidays.HongKong()
        else:
            # NYSE calendar (needs holidays>=0.34); older versions → weekday logic only
            cal = _holidays.financial_holidays("NYSE")
    except Exception:
        cal = set()
    _HOLIDAY_CACHE[market] = cal
    return cal


def _last_completed_session_from_local(market: str, local: datetime) -> date:
    """
    Last completed session given a **market-local naive** datetime.

    Accounts for the 16:00 close (+15 min buffer), weekends and exchange
    holidays. This is what "today" means when judging data freshness —
    comparing against the plain calendar date would cry "延遲 1 日" every
    morning before the open.
    """
    day = local.date()
    if (local.hour, local.minute) < (_CLOSE_HOUR, _CLOSE_BUFFER_MIN):
        # Today's session has not closed yet → the last completed one is earlier.
        day -= timedelta(days=1)

    cal = _market_holidays(market)
    for _ in range(30):  # bounded walk-back (longest holiday break < 10 days)
        if day.weekday() < 5 and day not in cal:
            return day
        day -= timedelta(days=1)
    return day


def last_completed_session(market: str, now: Optional[datetime] = None) -> date:
    """
    Most recent trading session that should already have closing data.

    ``now`` must be tz-aware (UTC assumed when naive); None means the current
    UTC time. Holidays come from the NYSE / HKEX calendars when the
    ``holidays`` package is installed.
    """
    return _last_completed_session_from_local(market, market_now(market, now))


def _last_scan_reference_local(market: str, now: Optional[datetime] = None) -> datetime:
    """Most recent scheduled daily scan time, as a **market-local naive** datetime."""
    local = market_now(market, now)
    hour, minute = _SCAN_TIME.get(market, (9, 10))
    ref = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if local < ref:
        ref -= timedelta(days=1)
    return ref


def expected_signal_date(market: str, now: Optional[datetime] = None) -> date:
    """
    The signal trading day the most recent scheduled scan *should* have produced.

    The daily scan runs **before the open**, so it can only see the previous
    completed session's close. Hence we evaluate the session relative to the
    last scan time instead of at "now" — otherwise every trading afternoon
    would falsely show "資料延遲 1 日".
    """
    return _last_completed_session_from_local(
        market, _last_scan_reference_local(market, now)
    )



def signal_freshness(market: str, now: Optional[datetime] = None) -> dict:
    """
    Freshness of one market's signal file, compared to the expected signal day.

    Returns a dict::

        {
          "market": "us",
          "signal_date": "2026-10-26" | None,
          "expected": "2026-10-26",     # what the last scheduled scan should have produced
          "lag_days": 1,                # 0 when fresh, None when unknown
          "is_stale": True,
        }
    """
    sig = signal_date(market)
    expected = expected_signal_date(market, now)

    result = {
        "market": market,
        "signal_date": sig,
        "expected": expected.isoformat(),
        "lag_days": None,
        "is_stale": False,
    }
    if not sig:
        return result
    try:
        sig_day = datetime.strptime(sig, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return result

    lag = (expected - sig_day).days
    result["lag_days"] = max(lag, 0)
    result["is_stale"] = lag > 0
    return result

