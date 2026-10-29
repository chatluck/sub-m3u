# sub-m3u

用 GitHub Actions 每 30 分钟抓取直播源订阅 → 去重 → 健康检查 → 发布成静态 `sub.m3u`，播放器直接订阅 Pages 地址。不需要任何常开设备。

```
Actions(cron)  ──►  srcpool.py --worker  ──►  gh-pages/sub.m3u  ──►  播放器
```

## 部署：5 步

### 1. 建仓库

GitHub 网页 → New repository → **Public**（私有仓库用 Pages 需付费）→ 不要勾 README。

### 2. 推上去

```bash
cd ghsub
git init -b main
git add -A
git commit -m "init"
git remote add origin https://github.com/USER/REPO.git
git push -u origin main
```

### 3. 改来源

编辑 `.github/workflows/update.yml` 里的 `SOURCES`，每行一个订阅地址：

```yaml
      SOURCES: |
        https://dainoiptv.xyz/sub?1dIg88fh=m3u
        http://k.niu66.vip/9tgcid
```

### 4. 开 Pages

Settings → Pages → Source 选 **Deploy from a branch** → Branch 选 `gh-pages` → `/ (root)` → Save。
（`gh-pages` 分支在第一次 Actions 跑完后才出现，所以先做第 5 步再看这里。）

### 5. 跑一次

Actions → `update-subscription` → Run workflow。
约 3~5 分钟，完成后 `gh-pages` 分支出现，Pages 会给出地址：

```
https://USER.github.io/REPO/sub.m3u      ← 播放器订阅这个
https://USER.github.io/REPO/          ← 人看的统计页
```

## 参数

| 位置 | 参数 | 说明 |
|---|---|---|
| `update.yml` → `SOURCES` | 订阅地址 | 每行一个，支持任意多个，自动去重 |
| `update.yml` → `EXTEND_DAYS` | 3650 | 表里「到期日期」那行写多少天后 |
| 命令行 | `--check-workers 16` | 健康检查并发；Actions 上 16 大约 3 分钟跑完 1500 条 |
| 命令行 | `--include-dead` | 把死源也留下（默认只输出活的） |

## 本地也能跑

```bash
python3 srcpool.py --worker --source "订阅地址" --out sub.m3u --html index.html
# 或起服务
python3 srcpool.py --host 0.0.0.0 --port 8897 --source "订阅地址"
```

纯标准库，Python 3.9+ 都能跑，无第三方依赖。

## 说明

- **只输出存活源**：每轮抓取后并发探测每个流地址（`#EXTM3U` / `mpegurl` / TS 同步字节 / `ftyp` 判定），死的剔除。
- **占位条目会被过滤**：`cccimg.com` 之类的广告视频、"售后/QQ/到期时间"这类假频道不进表。
- **失败不覆盖**：一个源抓取失败只跳过它，其余照常；存活为 0 时整轮失败，不会把空表推上去。
- **Actions 的 cron 有 5~15 分钟延迟**，属正常；连续失败 GitHub 会在 60 天后自动停用 schedule，届时去 Actions 页面点一次 Run workflow 即可恢复。
- 发布到公开 Pages 意味着这些源地址是公开的，介意就把仓库设为私有 + 用 Cloudflare Pages 之类的私有托管。

## 文件

| 文件 | 作用 |
|---|---|
| `srcpool.py` | 全部逻辑：解析、去重、健康检查、生成 m3u / 统计页 |
| `.github/workflows/update.yml` | 定时任务 + 发布到 gh-pages |
