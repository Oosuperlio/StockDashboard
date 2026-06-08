#!/bin/bash
# signal_scan_runner.sh — 清除緩存 → 掃描信號 → 提交到 GitHub → 觸發 Railway 部署
#
# 用法：./scripts/signal_scan_runner.sh [args...]
# 會透傳參數給 signal_scanner.py 並附加 --save

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR"

echo "🔁 [$(date '+%Y-%m-%d %H:%M:%S %Z')] 信號掃描啟動..."

# 步驟 1：清除 Python 位元組碼緩存（確保不使用舊 .pyc）
echo "🧹 清除 Python 位元組碼緩存..."
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find . -type f -name "*.pyc" -delete 2>/dev/null || true

# 步驟 2：強制重新生成 timestamp 檔案
touch "$SCRIPT_DIR"/**/*.py 2>/dev/null || true

# 步驟 3：以純淨進程運行（-B 禁止寫入新 pyc），自動附加 --save
echo "🚀 啟動 signal_scanner.py $* --save"
echo "---"
/usr/bin/python3 -B "$SCRIPT_DIR/signal_scanner.py" "$@" --save
SCAN_EXIT=$?

echo "---"
echo "🔁 [$(date '+%Y-%m-%d %H:%M:%S %Z')] 掃描完成 (exit=$SCAN_EXIT)"

# 步驟 4：提交信號 CSV 到 GitHub，觸發 Railway 自動部署
if [ -d "$SCRIPT_DIR/data/signals" ]; then
    echo "📤 提交信號數據到 GitHub..."
    cd "$SCRIPT_DIR"
    git add data/signals/latest_signals_*.csv data/signals/.gitkeep 2>/dev/null || true

    # 只在有變更時才 commit + push
    if ! git diff --cached --quiet; then
        git commit -m "🤖 auto: update daily signal results [$(date '+%Y-%m-%d %H:%M')]"
        echo "🚂 推送至 GitHub → 觸發 Railway 部署..."
        git push origin main 2>&1 || echo "⚠️ git push 失敗（可能無變更或網路問題）"
    else
        echo "⏭️  信號數據無變更，跳過 git push"
    fi
else
    echo "⚠️ data/signals/ 目錄不存在，跳過 git push"
fi

echo "✅ [$(date '+%Y-%m-%d %H:%M:%S %Z')] 完成"
exit $SCAN_EXIT
