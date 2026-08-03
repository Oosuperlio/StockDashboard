#!/bin/bash
# daily_downloader.sh — Hermes cron wrapper for daily_downloader.py
#
# IMPORTANT (2026-08-02): Must use SYSTEM python3 (/usr/bin/python3, 3.9).
# The Hermes venv Python 3.11 has numpy 2.4.4 which silently corrupts
# yfinance data (every close written as $0.00) — see skill
# "yfinance-duckdb-daily-loader" section 1 (Phase B). Verified on 2026-08-02:
# venv run wrote 939 zero-price rows; system python3 returns valid prices.

cd /Users/aiagent/projects/dashboard || exit 1

# Strip any inherited PYTHONPATH that points to system site-packages (Python 3.9)
# to avoid importing incompatible numpy C extensions.
unset PYTHONPATH

# System python3 (3.9, numpy 2.0.2) works correctly despite deprecation warnings.
# Do NOT switch back to the Hermes venv python — it causes silent $0 data corruption.
exec /usr/bin/python3 scripts/daily_downloader.py
