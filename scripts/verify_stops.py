#!/usr/bin/env python3
"""Verify the dynamic stops monitoring system integrity."""
import json, os, sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
errors = []

# 1. Check config
cfg_path = os.path.join(BASE, "data", "dynamic_stops", "stop_config.json")
if not os.path.exists(cfg_path):
    errors.append("Missing: stop_config.json")
else:
    with open(cfg_path) as f:
        cfg = json.load(f)
    tickers = cfg.get("tickers", {})
    print(f"✅ stop_config.json: {len(tickers)} tickers")
    for tk, data in tickers.items():
        sl = data.get("stop_loss", {})
        tp = data.get("take_profit", {}).get("tiers", [])
        print(f"  {tk}: SL=${sl.get('price','?'):>6} ({sl.get('pct_from_price','?'):>+5}%) | TP1=${tp[0].get('price','?'):>6} ({tp[0].get('pct_from_price','?'):>+5}%)" if tp else f"  {tk}: SL=${sl.get('price','?')}")

# 2. Check dash tab exists
tab_path = os.path.join(BASE, "dynamic_stops_tab.py")
if os.path.exists(tab_path):
    print(f"✅ dynamic_stops_tab.py exists ({os.path.getsize(tab_path)} bytes)")
else:
    errors.append("Missing: dynamic_stops_tab.py")

# 3. Check monitor script
mon_path = os.path.join(BASE, "scripts", "stop_monitor.py")
if os.path.exists(mon_path):
    print(f"✅ scripts/stop_monitor.py exists ({os.path.getsize(mon_path)} bytes)")
else:
    errors.append("Missing: scripts/stop_monitor.py")

# 4. Check app.py import
app_path = os.path.join(BASE, "app.py")
with open(app_path) as f:
    content = f.read()
if "from dynamic_stops_tab import render_dynamic_stops_tab" in content:
    print("✅ app.py imports dynamic_stops_tab")
else:
    errors.append("app.py missing import")
if '"stops"' in content:
    print("✅ app.py has stops page routing")
else:
    errors.append("app.py missing stops routing")

# 5. Check supporting data files
for fname in ["support_resistance.json", "atr_backtest.json", "ema_backtest.json"]:
    fpath = os.path.join(BASE, "data", "dynamic_stops", fname)
    if os.path.exists(fpath):
        sz = os.path.getsize(fpath)
        print(f"✅ data/dynamic_stops/{fname} ({sz} bytes)")
    else:
        errors.append(f"Missing: data/dynamic_stops/{fname}")

print()
if errors:
    print("❌ ERRORS:")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
else:
    print("🎉 ALL CHECKS PASSED — system is ready!")
