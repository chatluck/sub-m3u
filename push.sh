#!/bin/sh
# 推送本仓库到 GitHub 并给出启用 Pages 的步骤。
# 用法：
#   ./push.sh USER REPO             # 直连
#   ./push.sh USER REPO 7890        # 走本地代理端口（socks5h）
#
set -e
[ -n "$1" ] && [ -n "$2" ] || { echo "用法: ./push.sh USER REPO [代理端口]"; exit 1; }
USER="$1"; REPO="$2"; PROXY_PORT="$3"

URL="https://github.com/$USER/$REPO.git"
echo "远端: $URL"

git remote remove origin 2>/dev/null || true
git remote add origin "$URL"

if [ -n "$PROXY_PORT" ]; then
    echo "使用代理 127.0.0.1:$PROXY_PORT"
    git -c http.proxy="socks5h://127.0.0.1:$PROXY_PORT" push -u origin main
else
    git push -u origin main
fi

cat <<'EOF'

推送完成。接下来两步（网页操作）：

1. Actions → update-subscription → Run workflow（第一次手动触发）
2. Settings → Pages → Source: Deploy from a branch → Branch: gh-pages → / (root) → Save

订阅地址（把 USER/REPO 换成你的）：
  https://USER.github.io/REPO/sub.m3u
EOF
