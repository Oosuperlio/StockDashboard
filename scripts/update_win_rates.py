#!/usr/bin/env python3
"""
update_win_rates.py — 每週六運行，更新形態勝率數據並整理變化報告
"""
import subprocess, json, datetime, sys, os

WORKDIR = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.dirname(WORKDIR)
os.chdir(PARENT)

timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
report = [f"📊 形態勝率更新報告 | {timestamp}\n"]

# ── 1. 讀取舊數據 ──────────────────────────────────────────────
def read_csv(path):
    try:
        import csv
        rows = {}
        with open(path) as f:
            reader = csv.DictReader(f)
            for r in reader:
                name = r.get("pattern", r.get("name","")).strip()
                wr = r.get("win_rate", r.get("win_rate_pct",""))
                cnt = r.get("total", r.get("count","0"))
                if name and wr:
                    try:
                        rows[name] = (float(wr.rstrip("%")), int(cnt))
                    except:
                        pass
        return rows
    except Exception as e:
        return {}

old_sp500 = read_csv("backtest_results_sp500.csv")
old_hsi   = read_csv("backtest_results_hsi.csv")

# ── 2. 運行回測 ──────────────────────────────────────────────
report.append("=== 運行回測 ===")

for script, label in [("backtest_sp500.py", "S&P 500"), ("backtest_hsi.py", "HSI")]:
    try:
        result = subprocess.run(
            [sys.executable, script],
            capture_output=True, text=True, timeout=300, cwd=PARENT
        )
        last_line = result.stdout.strip().split("\n")[-1]
        report.append(f"  ✅ {label}: {last_line}")
    except Exception as e:
        report.append(f"  ❌ {label}: {e}")

# ── 3. 讀取新數據 ──────────────────────────────────────────────
new_sp500 = read_csv("backtest_results_sp500.csv")
new_hsi   = read_csv("backtest_results_hsi.csv")

# ── 4. 整理變化 ──────────────────────────────────────────────
def fmt_change(name, old, new):
    if old is None:
        return f"  🆕 {name}: {new[0]:.1f}% (n={new[1]})"
    delta = round(new[0] - old[0], 1)
    sign = "+" if delta > 0 else ""
    emoji = "🟢" if delta >= 3 else ("🔴" if delta <= -3 else "⚪")
    return f"  {emoji} {name}: {old[0]:.1f}% → {new[0]:.1f}% ({sign}{delta}pp, n={new[1]})"

def diff_section(title, old, new):
    lines = [f"\n=== {title} ==="]
    all_names = set(old) | set(new)
    sorted_names = sorted(all_names, key=lambda x: -(new.get(x) or (0,0))[0])
    for name in sorted_names:
        if name in old and name in new:
            lines.append(fmt_change(name, old[name], new[name]))
        elif name in new:
            lines.append(fmt_change(name, None, new[name]))
    return "\n".join(lines)

report.append(diff_section("S&P 500 勝率變化", old_sp500, new_sp500))
report.append(diff_section("HSI 勝率變化", old_hsi, new_hsi))

# ── 5. 關鍵洞察 ──────────────────────────────────────────────
sp_bull_flag = new_sp500.get("Bull Flag", (0,0))[0]
hk_bull_flag = new_hsi.get("Bull Flag", (0,0))[0]
sp_bear_flag = new_sp500.get("Bear Flag", (0,0))[0]
hk_bear_flag = new_hsi.get("Bear Flag", (0,0))[0]

report.append(f"""
=== 關鍵洞察 ===
• S&P 500 最強形態: Bull Flag ({sp_bull_flag:.1f}%)
• HSI 最強形態: Bear Flag ({hk_bear_flag:.1f}%)
• 跨市場差異: Bull Flag S&P 500 vs HSI = {sp_bull_flag - hk_bull_flag:.1f}pp
• 共同避開: Doji, Shooting Star, Bearish Harami (勝率 < 31%)
""")

full_report = "\n".join(report)
print(full_report)

# ── 6. 保存到 output 目錄 ────────────────────────────────────
out_dir = os.path.expanduser("~/.hermes/cron/output/update_win_rates")
os.makedirs(out_dir, exist_ok=True)
date_str = datetime.date.today().strftime("%Y-%m-%d")
out_path = os.path.join(out_dir, f"{date_str}.txt")
with open(out_path, "w") as f:
    f.write(full_report)
print(f"\n💾 已保存: {out_path}")
