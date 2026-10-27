"""AppTest 驗證：首頁「📡 信號總覽」卡片必須同時顯示兩個獨立時間，且日期來自
CSV `date` 欄（不是檔案 mtime / 容器 build 時間）。

驗證點：
  1. 卡片含「📅 信號交易日：」且日期 == CSV date 欄最大值（不是 mtime 日期）
  2. 卡片含「🕒 資料更新：」且為 <YYYY-MM-DD HH:MM> HKT 格式
  3. 舊式「📡 信號總覽 (YYYY-MM-DD)」標題已不存在
  4. 沒有 AppTest exception

Run: cd ~/projects/dashboard && .venv-apptest2/bin/python scripts/apptest_signal_dates.py
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from streamlit.testing.v1 import AppTest

from database.signals import signal_date, signal_freshness, data_update_time_str

CSV = "data/signals/latest_signals_all.csv"
US_CSV = "data/signals/latest_signals_us.csv"
HK_CSV = "data/signals/latest_signals_hk.csv"


def csv_max_date(path):
    df = pd.read_csv(path, usecols=["date"])
    return pd.to_datetime(df["date"], errors="coerce").max().strftime("%Y-%m-%d")


print("─" * 60)
print("前置檢查：CSV date 欄 vs mtime")
expected_us = csv_max_date(US_CSV)
expected_hk = csv_max_date(HK_CSV)
print(f"  CSV max date  US={expected_us}  HK={expected_hk}")
print(f"  signal_date() US={signal_date('us')}  HK={signal_date('hk')}")
for m in ("us", "hk"):
    f = signal_freshness(m)
    print(f"  freshness {m}: signal={f['signal_date']} expected={f['expected']} "
          f"lag={f['lag_days']} stale={f['is_stale']}")
print(f"  資料更新：{data_update_time_str()}")
assert signal_date("us") == expected_us, "signal_date('us') 不等於 CSV date 最大值"
assert signal_date("hk") == expected_hk, "signal_date('hk') 不等於 CSV date 最大值"

print("─" * 60)
at = AppTest.from_file("app.py", default_timeout=180)
at.run()

exceptions = [e.value for e in at.exception]
assert not exceptions, f"app.py 有 exception: {exceptions}"
print("PASS: 首載無 exception")

md = [m.value for m in at.markdown]

# ── 1) 新版卡片存在 ──
header = [m for m in md if "📡 信號總覽" in m]
assert header, f"FAIL: 找不到信號總覽卡片（markdown 樣本: {[m[:60] for m in md[:8]]}）"
card = header[0]
print(f"卡片 HTML:\n  {card}\n")

assert "📅 信號交易日：" in card, "FAIL: 缺少「📅 信號交易日」"
assert "🕒 資料更新：" in card, "FAIL: 缺少「🕒 資料更新」"

# ── 2) 舊式「(YYYY-MM-DD)」標題必須消失 ──
assert not re.search(r"📡 信號總覽 \(\d{4}-\d{2}-\d{2}\)", card), \
    "FAIL: 舊式「信號總覽 (日期)」仍在"
print("PASS: 舊式單一日期標題已移除（兩個獨立時間並列）")

# ── 3) 信號交易日 == CSV max date ──
m = re.search(r"📅 信號交易日：([^<]+)", card)
assert m, "FAIL: 無法解析信號交易日"
shown = m.group(1).strip()
print(f"顯示的信號交易日: {shown}")
assert expected_us in shown, f"FAIL: 信號交易日未顯示 {expected_us}（實際 {shown}）"

# ── 4) 資料更新時間格式 ──
m2 = re.search(r"🕒 資料更新：([^<]+)", card)
assert m2, "FAIL: 無法解析資料更新時間"
upd = m2.group(1).strip()
print(f"顯示的資料更新時間: {upd}")
assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2} HKT", upd), \
    f"FAIL: 資料更新時間格式不符（實際 {upd}）"

# ── 5) 延遲警示（若有）必須是橙色 warn-delay ──
stale = [m for m in md if "資料延遲" in m]
if stale:
    print(f"延遲警示: {[re.sub('<[^>]+>', '', s) for s in stale]}")
    assert "warn-delay" in stale[0], "FAIL: 延遲警示缺少 .warn-delay 樣式"
else:
    print("延遲警示: 無（兩個市場皆為最新）")

# ── 6) 板塊加總（對照線上核對用）──
sector_rows = [m for m in md if "sector-stat-name" in m]
print(f"板塊卡片數: {len(sector_rows)}")
print(f"美股信號檔數: {len(pd.read_csv(US_CSV))} ｜ 港股: {len(pd.read_csv(HK_CSV))}")
print(f"首頁總數 (共 N 個) = {len(pd.read_csv(US_CSV)) + len(pd.read_csv(HK_CSV))}")
print("\n=== ALL CHECKS PASSED ===")
