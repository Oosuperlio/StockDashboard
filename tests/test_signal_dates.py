"""
Tests for signal_date() / data-freshness helpers in database/signals.py.

Root-cause regression: the "📡 信號總覽" header used to be derived from the CSV
file mtime. Inside the Docker container mtime == build time (UTC), so a
one-day-old dataset was labelled with *today's* date and real data delay was
masked (2026-10-27 incident).

Run:
    pytest tests/test_signal_dates.py -v
"""

import datetime as dt
import os
from pathlib import Path

import pandas as pd
import pytest

from database import signals as S


# ─── helpers ────────────────────────────────────────────────────────────────

def _write_signal_csv(path: Path, dates, symbols=None, with_date_col=True):
    """Write a minimal signal CSV; extra columns are irrelevant to the helpers."""
    path.parent.mkdir(parents=True, exist_ok=True)
    symbols = symbols or [f"T{i}" for i in range(len(dates))]
    if with_date_col:
        pd.DataFrame({"symbol": symbols, "date": dates}).to_csv(path, index=False)
    else:
        pd.DataFrame({"symbol": symbols}).to_csv(path, index=False)


def _set_mtime(path: Path, utc: dt.datetime):
    ts = utc.replace(tzinfo=dt.timezone.utc).timestamp()
    os.utime(path, (ts, ts))


@pytest.fixture
def signals_dir(tmp_path, monkeypatch):
    """Redirect the module at a tmp directory and return it."""
    monkeypatch.setattr(S, "SIGNALS_DIR", tmp_path)
    monkeypatch.setattr(S, "DATA_UPDATE_REPORT", tmp_path.parent / "last_update_report.txt")
    return tmp_path


# ─── signal_date(): must use the CSV `date` column, never mtime ─────────────

def test_signal_date_uses_csv_max_date_not_mtime(signals_dir):
    """mtime = 2026-10-27 (container build time) but data is from 2026-10-26."""
    path = signals_dir / "latest_signals_all.csv"
    _write_signal_csv(path, ["2026-10-23", "2026-10-26", "2026-10-22"])
    _set_mtime(path, dt.datetime(2026, 10, 27, 13, 33))

    assert S.signal_date() == "2026-10-26"  # NOT "2026-10-27"


def test_signal_date_parses_dates_when_lexical_order_differs(signals_dir):
    path = signals_dir / "latest_signals_all.csv"
    _write_signal_csv(path, ["2026-9-5", "2026-10-01", "2026-10-9"])
    _set_mtime(path, dt.datetime(2026, 10, 27, 13, 33))

    assert S.signal_date() == "2026-10-09"


def test_signal_date_per_market(signals_dir):
    _write_signal_csv(signals_dir / "latest_signals_us.csv", ["2026-10-23", "2026-10-26"])
    _write_signal_csv(signals_dir / "latest_signals_hk.csv", ["2026-10-21", "2026-10-22"])
    _write_signal_csv(signals_dir / "latest_signals_all.csv", ["2026-10-22", "2026-10-26"])

    assert S.signal_date("us") == "2026-10-26"
    assert S.signal_date("hk") == "2026-10-22"
    assert S.signal_date(None) == "2026-10-26"


def test_signal_date_falls_back_to_mtime_when_date_column_missing(signals_dir):
    path = signals_dir / "latest_signals_all.csv"
    _write_signal_csv(path, [], symbols=["AAA"], with_date_col=False)
    _set_mtime(path, dt.datetime(2026, 10, 20, 12, 0))

    assert S.signal_date() == "2026-10-20"


def test_signal_date_falls_back_to_mtime_when_dates_unparseable(signals_dir):
    path = signals_dir / "latest_signals_all.csv"
    _write_signal_csv(path, ["n/a", "—", ""])
    _set_mtime(path, dt.datetime(2026, 10, 18, 12, 0))

    assert S.signal_date() == "2026-10-18"


def test_signal_date_returns_none_when_file_missing(signals_dir):
    assert S.signal_date() is None
    assert S.signal_date("us") is None


# ─── data_update_time(): the second, independent timestamp ──────────────────

def test_data_update_time_picks_newest_mtime(signals_dir):
    all_csv = signals_dir / "latest_signals_all.csv"
    us_csv = signals_dir / "latest_signals_us.csv"
    _write_signal_csv(all_csv, ["2026-10-26"])
    _write_signal_csv(us_csv, ["2026-10-26"])
    _set_mtime(all_csv, dt.datetime(2026, 10, 26, 1, 15))
    _set_mtime(us_csv, dt.datetime(2026, 10, 27, 13, 33))

    assert S.data_update_time() == dt.datetime(2026, 10, 27, 13, 33, tzinfo=dt.timezone.utc)


def test_data_update_time_str_in_hkt(signals_dir):
    csv = signals_dir / "latest_signals_all.csv"
    _write_signal_csv(csv, ["2026-10-26"])
    _set_mtime(csv, dt.datetime(2026, 10, 27, 13, 33))

    assert S.data_update_time_str() == "2026-10-27 21:33 HKT"


def test_data_update_time_none_without_files(signals_dir):
    assert S.data_update_time() is None
    assert S.data_update_time_str() is None


# ─── Trading-session helpers ────────────────────────────────────────────────
# 2026-10-27 is a Tuesday; 2026-10-25 a Sunday; 2026-11-26 Thanksgiving.

def test_us_offset_handles_dst():
    assert S._us_utc_offset_hours(dt.date(2026, 7, 1)) == -4   # EDT
    assert S._us_utc_offset_hours(dt.date(2026, 12, 1)) == -5  # EST


def test_last_completed_session_before_close_is_previous_day():
    now = dt.datetime(2026, 10, 27, 13, 0, tzinfo=dt.timezone.utc)  # 09:00 ET
    assert S.last_completed_session("us", now) == dt.date(2026, 10, 26)


def test_last_completed_session_after_close_is_today():
    now = dt.datetime(2026, 10, 27, 21, 0, tzinfo=dt.timezone.utc)  # 17:00 ET
    assert S.last_completed_session("us", now) == dt.date(2026, 10, 27)


def test_last_completed_session_skips_weekend():
    now = dt.datetime(2026, 10, 25, 12, 0, tzinfo=dt.timezone.utc)  # Sunday
    assert S.last_completed_session("us", now) == dt.date(2026, 10, 23)


def test_last_completed_session_skips_holiday():
    # 2026-11-26 (Thu) = Thanksgiving → market closed at 16:00 ET but no session
    now = dt.datetime(2026, 11, 27, 2, 0, tzinfo=dt.timezone.utc)  # 21:00 EDT? → EST 21:00 Nov 26
    assert S.last_completed_session("us", now) == dt.date(2026, 11, 25)


def test_last_completed_session_hk_uses_hkt_close():
    # 2026-10-27 12:00 UTC = 20:00 HKT → HK session closed → today
    now = dt.datetime(2026, 10, 27, 12, 0, tzinfo=dt.timezone.utc)
    assert S.last_completed_session("hk", now) == dt.date(2026, 10, 27)


# ─── Staleness: no false alarm just because the scan is pre-open ────────────

def test_expected_signal_date_no_false_lag_after_close():
    """HK closed at 16:00; next scan (09:10 HKT) has not run yet → no lag."""
    now = dt.datetime(2026, 10, 27, 13, 46, tzinfo=dt.timezone.utc)  # 21:46 HKT
    assert S.expected_signal_date("hk", now) == dt.date(2026, 10, 26)


def test_expected_signal_date_before_morning_scan():
    now = dt.datetime(2026, 10, 27, 20, 0, tzinfo=dt.timezone.utc)  # 04:00 HKT Oct 28
    assert S.expected_signal_date("hk", now) == dt.date(2026, 10, 26)


def test_expected_signal_date_after_morning_scan_picks_up_new_session():
    now = dt.datetime(2026, 10, 28, 2, 0, tzinfo=dt.timezone.utc)  # 10:00 HKT Oct 28
    assert S.expected_signal_date("hk", now) == dt.date(2026, 10, 27)


def test_signal_freshness_fresh_is_not_stale(signals_dir):
    _write_signal_csv(signals_dir / "latest_signals_us.csv", ["2026-10-26"])
    now = dt.datetime(2026, 10, 27, 21, 0, tzinfo=dt.timezone.utc)  # 17:00 ET

    info = S.signal_freshness("us", now)
    assert info["signal_date"] == "2026-10-26"
    # The 09:10 ET scan runs pre-open → the freshest possible signal day is the
    # previous session (2026-10-26), even though today's session already closed.
    assert info["expected"] == "2026-10-26"
    assert info["lag_days"] == 0
    assert info["is_stale"] is False


def test_signal_freshness_detects_missed_scans(signals_dir):
    """Scan never refreshed after Friday → 3 missed sessions (Fri → Mon → Tue)."""
    _write_signal_csv(signals_dir / "latest_signals_us.csv", ["2026-10-23"])
    now = dt.datetime(2026, 10, 27, 21, 0, tzinfo=dt.timezone.utc)

    info = S.signal_freshness("us", now)
    assert info["expected"] == "2026-10-26"
    assert info["lag_days"] == 3
    assert info["is_stale"] is True


def test_signal_freshness_handles_missing_file(signals_dir):
    info = S.signal_freshness("hk")
    assert info["signal_date"] is None
    assert info["lag_days"] is None
    assert info["is_stale"] is False
