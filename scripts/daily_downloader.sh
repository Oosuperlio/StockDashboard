#!/bin/bash
# daily_downloader.sh — Hermes cron wrapper for daily_downloader.py
#
# Uses the Hermes venv Python 3.11 which has all dependencies installed (numpy 2.4.4,
# yfinance 1.3.0, duckdb 1.5.4). The system python3 (3.9) has incompatible numpy 2.0.2.
#
# Hermes cron agent may invoke python3 which resolves to system /usr/bin/python3 (3.9)
# and fails with: ImportError: No module named 'numpy._core._multiarray_umath'

cd /Users/aiagent/projects/dashboard || exit 1
HERMES_PYTHON="/Users/aiagent/.hermes/hermes-agent/venv/bin/python3"
HERMES_VENV="/Users/aiagent/.hermes/hermes-agent/venv"

if [ ! -x "$HERMES_PYTHON" ]; then
    echo "ERROR: Hermes venv python not found at $HERMES_PYTHON" >&2
    echo "Falling back to system python3 (may fail with numpy incompatibility)" >&2
    python3 scripts/daily_downloader.py 2>&1
    exit $?
fi

# Strip any inherited PYTHONPATH that points to system site-packages (Python 3.9)
# to avoid importing incompatible numpy C extensions.
unset PYTHONPATH

exec "$HERMES_PYTHON" scripts/daily_downloader.py
