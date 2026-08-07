#!/bin/bash
# fix_git_auth.sh — 修復 dashboard + quant-value-investor 的 GitHub 認證
#
# 用法：
#   ./fix_git_auth.sh --ssh              # 改用 SSH（需先將 id_ed25519.pub 加到 GitHub）
#   ./fix_git_auth.sh --token <file>     # 使用新 PAT（token 存於 <file>，第一行即 token）
#
# 背景：原 remote URL 格式錯誤（token 當 username）+ token 已失效（401），
#       導致 cron 環境下 git push 靜默失敗。此腳本一鍵修正。

set -euo pipefail

DASHBOARD=~/projects/dashboard
QV=~/projects/quant-value-investor
GITHUB_USER=Oosuperlio

echo "=== MMBH GitHub 認證修復 ==="

# ---------- SSH 模式 ----------
if [ "${1:-}" = "--ssh" ]; then
    echo "🔑 模式：SSH"
    echo "測試 SSH 連線...（需先將公鑰加到 GitHub）"
    # 注意：不能用 `ssh | grep -q` 管道 — pipefail 下 grep 提前退出會讓 ssh 吃到
    # SIGPIPE 並返回非零，導致整個條件誤判。先存變數再判斷。
    SSH_OUT=$(ssh -o BatchMode=yes -o ConnectTimeout=8 -T git@github.com 2>&1 || true)
    if echo "$SSH_OUT" | grep -q "successfully authenticated"; then
        echo "✅ SSH 認證有效"
        git -C "$DASHBOARD" remote set-url origin "git@github.com:${GITHUB_USER}/StockDashboard.git"
        git -C "$QV" remote set-url origin "git@github.com:${GITHUB_USER}/quant-value-investor.git"
        echo "✅ remote 已改為 SSH:"
        git -C "$DASHBOARD" remote get-url origin
        git -C "$QV" remote get-url origin
        echo "🚂 推送積壓 commits..."
        git -C "$DASHBOARD" push origin main 2>&1 && echo "✅ dashboard 推送成功" || echo "❌ dashboard 推送失敗"
        git -C "$QV" push origin main 2>&1 && echo "✅ quant-value-investor 推送成功" || echo "❌ quant-value-investor 推送失敗"
    else
        echo "❌ SSH 未授權 — 請先到 GitHub → Settings → SSH and GPG keys 添加："
        cat ~/.ssh/id_ed25519.pub
        exit 1
    fi

# ---------- PAT 模式 ----------
elif [ "${1:-}" = "--token" ] && [ -n "${2:-}" ]; then
    TOKEN_FILE="$2"
    if [ ! -f "$TOKEN_FILE" ]; then
        echo "❌ token 檔案不存在: $TOKEN_FILE" >&2
        exit 1
    fi
    NEW_TOKEN=$(head -1 "$TOKEN_FILE" | tr -d '[:space:]')
    if [ -z "$NEW_TOKEN" ]; then
        echo "❌ token 檔案為空" >&2
        exit 1
    fi
    echo "🔑 模式：PAT（token 來源: $TOKEN_FILE）"
    # 驗證 token
    HTTP=$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: token $NEW_TOKEN" https://api.github.com/user)
    if [ "$HTTP" = "200" ]; then
        echo "✅ token 有效（GitHub API 200）"
    else
        echo "❌ token 無效（GitHub API $HTTP）— 請確認 token 未過期且有 repo scope" >&2
        exit 1
    fi
    # 修正 remote URL（正確格式: https://USER:TOKEN@github.com/...）
    git -C "$DASHBOARD" remote set-url origin "https://${GITHUB_USER}:${NEW_TOKEN}@github.com/${GITHUB_USER}/StockDashboard.git"
    git -C "$QV" remote set-url origin "https://${GITHUB_USER}:${NEW_TOKEN}@github.com/${GITHUB_USER}/quant-value-investor.git"
    # 更新 credential store（供其他 repo 使用）
    git config --global credential.helper store
    echo "https://${GITHUB_USER}:${NEW_TOKEN}@github.com" > ~/.git-credentials
    chmod 600 ~/.git-credentials
    echo "✅ remote URL + credential store 已更新"
    echo "🚂 推送積壓 commits..."
    git -C "$DASHBOARD" push origin main 2>&1 && echo "✅ dashboard 推送成功" || echo "❌ dashboard 推送失敗"
    git -C "$QV" push origin main 2>&1 && echo "✅ quant-value-investor 推送成功" || echo "❌ quant-value-investor 推送失敗"

else
    echo "用法: $0 --ssh | --token <file>" >&2
    exit 1
fi

echo "=== 完成 ==="
