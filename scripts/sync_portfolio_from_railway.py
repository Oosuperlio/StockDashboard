#!/usr/bin/env python3
"""
sync_portfolio_from_railway.py

Pull the latest portfolio.json from the Railway persistent volume
and save it locally. Run whenever Edward updates positions via the
dashboard web UI.

Usage:
  python3 scripts/sync_portfolio_from_railway.py
"""
import json
import subprocess
import sys
from pathlib import Path

DASHBOARD_DIR = Path(__file__).resolve().parent.parent
LOCAL_PORTFOLIO = DASHBOARD_DIR / "data" / "portfolio" / "portfolio.json"


def sync_from_railway() -> bool:
    """SSH into Railway and download portfolio.json."""
    cmd = [
        "railway", "ssh", "--",
        "cat", "/app/data/portfolio/portfolio.json"
    ]
    raw_output = ""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=DASHBOARD_DIR,
        )
        if result.returncode != 0:
            print(f"❌ SSH failed: {result.stderr.strip()}")
            return False
        raw_output = result.stdout.strip()
    except subprocess.TimeoutExpired:
        print("❌ SSH timed out after 30s")
        return False
    except FileNotFoundError:
        print("❌ railway CLI not found (is Railway CLI installed?)")
        return False

    if not raw_output:
        raw_output = "[]"

    try:
        parsed = json.loads(raw_output)
    except json.JSONDecodeError as e:
        print(f"❌ Invalid JSON from Railway: {e}")
        print(f"   Raw output: {raw_output[:300]}")
        return False

    LOCAL_PORTFOLIO.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCAL_PORTFOLIO, "w") as f:
        json.dump(parsed, f, indent=2)

    print(f"✅ Synced {len(parsed)} positions from Railway")
    for p in parsed:
        ticker = p["ticker"]
        trades = len(p["trades"])
        net = sum(
            t["shares"] if t["type"] == "buy" else -t["shares"]
            for t in p["trades"]
        )
        status = "🟢 open" if net > 0 else "📦 closed"
        print(f"   {ticker}: {status}, {trades} trades, {abs(net):.1f} shares")
    print(f"   → {LOCAL_PORTFOLIO}")
    return True


if __name__ == "__main__":
    success = sync_from_railway()
    sys.exit(0 if success else 1)
