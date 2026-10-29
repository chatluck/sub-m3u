#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
srcpool.py —— 通用直播源池（适用于「直连公开源」型订阅，如 dainoiptv.xyz）

为什么要单独一个：
  dainoiptv 这类面板的流地址是公网直连（`http://116.233.166.4:85/...?key=txiptv`），
  不带账号、不带签名、不绑 IP —— 有效期只写在列表第 2 行那句「到期时间」里，
  对播放没有任何约束。所以把列表抓到本地、做健康检查、重新出表即可，
  本地表的「有效期」随便写。

功能：
  - 拉若干订阅（m3u / txt），解析 EXTINF 属性 + URL；
  - 按 URL 去重，保留名称/分组/logo/来源；
  - 并发健康检查（GET 源地址，200 且内容含 m3u8/ts/mp4 视为活）；
  - 只输出活的；死了不删，标记后下次复查可复活；
  - 到期日期行按 --extend-days/--fixed-date 重写。

用法：
  python3 srcpool.py --port 8897 \\
      --source "https://dainoiptv.xyz/sub?1dIg88fh=m3u" \\
      --extend-days 3650 --check-workers 24
  GET /sub.m3u /health /stats /check /add?url=&name=
"""

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
POOL_FILE = os.path.join(HERE, "srcpool.json")
LOCK = threading.Lock()
UA = "okhttp/3.12.0"

CFG = {"extend_days": 3650, "fixed_date": None, "refresh": 3600,
       "workers": 24, "timeout": 8, "only_alive": True}
SOURCES = {}
POOL = {}
STATE = {"err": "", "last_refresh": 0, "last_check": 0, "checking": False}

ATTR_RE = re.compile(r'([a-zA-Z0-9-]+)="([^"]*)"')


def fetch(url, timeout=25, retries=4):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
            text = data.decode("utf-8", "replace")
            if len(data) < 200:
                raise ValueError("响应过短 %d 字节" % len(data))
            return text
        except Exception as e:
            last = e
            if i < retries - 1:
                wait = 3 + i * 4
                print("[warn] %s 第 %d 次失败(%r)，%ds 后重试" % (url, i + 1, e, wait))
                time.sleep(wait)
    raise last


PLACEHOLDER_URL = ("cccimg.com", "placeholder-status", "example.com")
PLACEHOLDER_NAME = ("售后", "QQ:", "到期日期", "到期时间", "购买", "续费")


def is_placeholder(url, cur):
    if any(m in url for m in PLACEHOLDER_URL):
        return True
    text = (cur.get("name", "") + " " + (cur.get("group") or ""))
    return any(m in text for m in PLACEHOLDER_NAME)


def parse(text):
    """解析 m3u/txt，返回 [{name,group,logo,url}]"""
    out, cur = [], {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXTINF"):
            attrs = dict(ATTR_RE.findall(line))
            name = line.split(",", 1)[-1].strip() if "," in line else attrs.get("tvg-name", "")
            cur = {"name": name, "group": attrs.get("group-title", ""),
                   "logo": attrs.get("tvg-logo", "")}
        elif line.startswith("#"):
            continue
        else:
            url = line
            if url.startswith(("http://", "https://", "rtmp://", "rtsp://")) and not is_placeholder(url, cur):
                item = {"url": url, "name": cur.get("name", ""),
                        "group": cur.get("group", ""), "logo": cur.get("logo", "")}
                out.append(item)
            cur = {}
    return out


def alive_check(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Range": "bytes=0-2048"})
        with urllib.request.urlopen(req, timeout=CFG["timeout"]) as r:
            if r.getcode() >= 400:
                return False
            head = r.read(2048)
            ctype = (r.headers.get("Content-Type") or "").lower()
        if b"#EXTM3U" in head or b"#EXTINF" in head:
            return True
        if "mpegurl" in ctype or "video" in ctype:
            return True
        if head[:1] == b"\x47" and len(head) >= 188:      # MPEG-TS 同步字节
            return True
        if head[4:8] == b"ftyp":                           # mp4
            return True
        return len(head) > 0
    except Exception:
        return False


def refresh():
    STATE["err"] = ""
    for name, url in list(SOURCES.items()):
        try:
            text = fetch(url)
            items = parse(text)
            if not items:
                raise ValueError("解析到 0 条")
            with LOCK:
                for it in items:
                    k = it["url"]
                    rec = POOL.get(k)
                    if rec:
                        rec.update({x: it[x] for x in ("name", "group", "logo") if it[x]})
                        rec["src"] = name
                    else:
                        it.update({"src": name, "alive": None, "last": 0})
                        POOL[k] = it
            print("[ok] %s 解析 %d 条" % (name, len(items)))
        except Exception as e:
            STATE["err"] += "%s: %r; " % (name, e)
            print("[warn] %s 失败: %r" % (name, e))
    STATE["last_refresh"] = time.time()
    save()
    return len(POOL)


def check(limit=0):
    with LOCK:
        keys = list(POOL.keys())
    if limit:
        keys = keys[:limit]
    STATE["checking"] = True
    ok_n = 0
    try:
        with cf.ThreadPoolExecutor(max_workers=CFG["workers"]) as ex:
            futs = {ex.submit(alive_check, k): k for k in keys}
            for i, fu in enumerate(cf.as_completed(futs), 1):
                k = futs[fu]
                ok = False
                try:
                    ok = fu.result()
                except Exception:
                    pass
                with LOCK:
                    if k in POOL:
                        POOL[k]["alive"] = ok
                        POOL[k]["last"] = time.time()
                ok_n += 1 if ok else 0
                if i % 200 == 0:
                    print("[..] 已检查 %d/%d，活 %d" % (i, len(keys), ok_n), flush=True)
    finally:
        STATE["checking"] = False
        STATE["last_check"] = time.time()
        save()
    print("[ok] 检查完成 %d 条，活 %d" % (len(keys), ok_n))
    return ok_n


def save():
    with LOCK:
        data = {"sources": SOURCES, "cfg": {k: CFG[k] for k in ("extend_days", "fixed_date")},
                "pool": POOL}
    tmp = POOL_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, POOL_FILE)


def load():
    if not os.path.exists(POOL_FILE):
        return
    try:
        d = json.load(open(POOL_FILE, encoding="utf-8"))
        SOURCES.update(d.get("sources") or {})
        POOL.update(d.get("pool") or {})
        print("[ok] 载入 %d 条源 / %d 个订阅" % (len(POOL), len(SOURCES)))
    except Exception as e:
        print("[warn] 池文件损坏: %r" % (e,))


def m3u(groups=None):
    with LOCK:
        items = list(POOL.values())
    if CFG["only_alive"]:
        items = [i for i in items if i.get("alive") is True or i.get("alive") is None]
    if groups:
        items = [i for i in items if i.get("group") in groups]
    items.sort(key=lambda i: (i.get("group") or "~", i.get("name") or ""))
    expire = CFG["fixed_date"] or (
        dt.datetime.now() + dt.timedelta(days=CFG["extend_days"])).strftime("%Y-%m-%d %H:%M")
    out = ["#EXTM3U",
           '#EXTINF:-1 tvg-name="到期日期：%s" group-title="到期日期",到期日期：%s' % (expire, expire),
           "http://127.0.0.1/placeholder-status"]
    for i in items:
        a = 'tvg-name="%s"' % (i.get("name") or "")
        if i.get("logo"):
            a += ' tvg-logo="%s"' % i["logo"]
        a += ' group-title="%s"' % (i.get("group") or "")
        out.append("#EXTINF:-1 %s,%s" % (a, i.get("name") or "未命名"))
        out.append(i["url"])
    return ("\n".join(out) + "\n").encode("utf-8")


def write_html(path):
    import html as _h
    with LOCK:
        items = list(POOL.values())
    alive = [i for i in items if i.get("alive") is True]
    dead = [i for i in items if i.get("alive") is False]
    g = {}
    for i in alive:
        k = i.get("group") or "(无分组)"
        g[k] = g.get(k, 0) + 1
    rows = "".join("<tr><td>%s</td><td>%d</td></tr>" % (_h.escape(k), v)
                   for k, v in sorted(g.items(), key=lambda x: -x[1]))
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    doc = """<!doctype html><meta charset="utf-8"><title>sub.m3u</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{font:15px/1.6 -apple-system,system-ui,sans-serif;max-width:720px;margin:40px auto;padding:0 16px;color:#222}
code{background:#f2f2f2;padding:2px 6px;border-radius:4px}
table{border-collapse:collapse;width:100%%;margin-top:16px}
td,th{border-bottom:1px solid #eee;padding:6px 4px;text-align:left;font-size:14px}
.k{color:#888;font-size:13px}</style>
<h2>直播源订阅</h2>
<p class="k">生成时间 %s</p>
<p>订阅地址：<code>https://USER.github.io/REPO/sub.m3u</code></p>
<p>存活 <b>%d</b> 条 / 已剔除 <b>%d</b> 条 / 分组 <b>%d</b> 个</p>
<table><tr><th>分组</th><th>存活</th></tr>%s</table>
""" % (now, len(alive), len(dead), len(g), rows)
    with open(path, "w", encoding="utf-8") as f:
        f.write(doc)
    print("[ok] 写出 %s" % path)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        p = u.path
        if p in ("/sub.m3u", "/sub", "/"):
            self._send(200, m3u(), "audio/x-mpegurl; charset=utf-8")
        elif p == "/health":
            with LOCK:
                t = len(POOL)
                a = sum(1 for v in POOL.values() if v.get("alive") is True)
                d = sum(1 for v in POOL.values() if v.get("alive") is False)
            self._send(200, json.dumps({"total": t, "alive": a, "dead": d,
                                        "unknown": t - a - d, "checking": STATE["checking"],
                                        "sources": list(SOURCES), "err": STATE["err"]},
                                       ensure_ascii=False).encode())
        elif p == "/stats":
            with LOCK:
                g = {}
                for v in POOL.values():
                    key = v.get("group") or "(无分组)"
                    g.setdefault(key, [0, 0])
                    g[key][0] += 1
                    g[key][1] += 1 if v.get("alive") else 0
            self._send(200, json.dumps(g, ensure_ascii=False, indent=1).encode())
        elif p == "/add":
            url = (q.get("url") or [""])[0]
            name = (q.get("name") or ["src%d" % (len(SOURCES) + 1)])[0]
            if not url:
                return self._send(400, b'{"ok":false,"msg":"need url"}')
            SOURCES[name] = url
            threading.Thread(target=lambda: (refresh(), check(), save()), daemon=True).start()
            self._send(200, json.dumps({"ok": True, "added": name}).encode())
        elif p == "/check":
            threading.Thread(target=check, daemon=True).start()
            self._send(200, b'{"ok":true,"msg":"checking"}')
        else:
            self._send(404, b'{"ok":false}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8897)
    ap.add_argument("--source", action="append", default=[])
    ap.add_argument("--extend-days", type=int, default=3650)
    ap.add_argument("--fixed-date", default=None)
    ap.add_argument("--refresh", type=int, default=3600)
    ap.add_argument("--check-workers", type=int, default=24)
    ap.add_argument("--include-dead", action="store_true")
    ap.add_argument("--check-only", action="store_true", help="跑一轮就退出")
    ap.add_argument("--worker", action="store_true", help="worker 模式：跑一轮，写文件，退出（不起服务）")
    ap.add_argument("--no-check", action="store_true", help="跳过健康检查，只合并去重（跨境环境推荐，快 10 倍）")
    ap.add_argument("--out", default=None, help="m3u 输出路径（--worker 时写文件）")
    ap.add_argument("--html", default=None, help="同时输出一个统计页 index.html")
    a = ap.parse_args()

    CFG.update(extend_days=a.extend_days, fixed_date=a.fixed_date, refresh=a.refresh,
               workers=a.check_workers, only_alive=not a.include_dead)
    load()
    env_src = [x.strip() for x in re.split(r"[,\n]", os.environ.get("SOURCES", "")) if x.strip()]
    for i, s in enumerate(a.source + env_src):
        SOURCES["src%d" % (i + 1)] = s

    if a.check_only or a.worker:
        refresh()
        if a.no_check:
            print("[ok] 跳过健康检查，合并模式")
        else:
            check()
        if a.worker:
            body = m3u()
            out = a.out or "sub.m3u"
            d = os.path.dirname(os.path.abspath(out))
            os.makedirs(d, exist_ok=True)
            with open(out, "wb") as f:
                f.write(body)
            alive = sum(1 for v in POOL.values() if v.get("alive") is True)
            total = len(POOL)
            print("[ok] 写出 %s（%d 条，池 %d，活 %d）" % (out, body.count(b"#EXTINF") - 1, total, alive))
            if a.html:
                write_html(a.html)
            if a.no_check:
                if total == 0:
                    raise SystemExit("一个源都没抓到，本次不发布")
            elif alive == 0:
                raise SystemExit("没有存活源，本次不发布")
        return

    def loop():
        while True:
            try:
                refresh()
                check()
            except Exception as e:
                print("[warn] 循环异常: %r" % (e,))
            time.sleep(CFG["refresh"])

    if a.source or not POOL:
        threading.Thread(target=loop, daemon=True).start()

    srv = ThreadingHTTPServer((a.host, a.port), H)
    print("订阅: http://%s:%d/sub.m3u  池: %d 条" % (a.host, a.port, len(POOL)))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
