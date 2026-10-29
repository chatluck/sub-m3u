#!/bin/bash
# 网络恢复后自动完成：等 Actions 产出 gh-pages → 建 Pages → 验证订阅地址
export PATH="/opt/homebrew/bin:$PATH"
REPO="chatluck/sub-m3u"
log() { echo "[$(date +%H:%M:%S)] $*"; }

log "等待 GitHub API 恢复..."
for i in $(seq 1 200); do
  if curl -sS -o /dev/null --max-time 8 https://api.github.com/rate_limit 2>/dev/null; then
    log "API 已通（第 $i 次尝试）"; break
  fi
  sleep 15
done

log "当前 workflow 运行状态："
gh run list --limit 3 2>&1 | head -5

log "等待 gh-pages 分支出现（最多 15 分钟）..."
for i in $(seq 1 45); do
  if git ls-remote --heads origin gh-pages 2>/dev/null | grep -q gh-pages; then
    log "gh-pages 已存在"; break
  fi
  sleep 20
done

log "创建/更新 Pages 源..."
if gh api "repos/$REPO/pages" >/dev/null 2>&1; then
  gh api -X PUT "repos/$REPO/pages" -f "source[branch]=gh-pages" -f "source[path]=/" >/dev/null 2>&1 && log "Pages 已更新"
else
  gh api -X POST "repos/$REPO/pages" -f "source[branch]=gh-pages" -f "source[path]=/" >/dev/null 2>&1 && log "Pages 已创建"
fi

log "等待 Pages 构建..."
for i in $(seq 1 30); do
  code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "https://chatluck.github.io/sub-m3u/sub.m3u" 2>/dev/null)
  log "探测 sub.m3u -> $code"
  [ "$code" = "200" ] && break
  sleep 20
done

log "==== 结果 ===="
echo "订阅: https://chatluck.github.io/sub-m3u/sub.m3u"
echo "统计: https://chatluck.github.io/sub-m3u/"
curl -sS --max-time 15 "https://chatluck.github.io/sub-m3u/sub.m3u" -o /tmp/final_sub.m3u 2>/dev/null && \
  echo "条数: $(grep -c '#EXTINF' /tmp/final_sub.m3u)" && sed -n '2p' /tmp/final_sub.m3u
