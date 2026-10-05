#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""批量下载文献全文 PDF —— 通过真实浏览器 + 机构订阅会话。

用法:
  # 1) 先启动受控浏览器（另开一个终端）
  powershell -ExecutionPolicy Bypass -File launch_edge.ps1 -StartUrl "https://ds.carsi.edu.cn/"

  # 2) 让用户在该窗口里完成统一身份认证登录（Agent 不经手密码）

  # 3) 探测机构授权状态
  python fetch.py --check

  # 4) 批量下载
  python fetch.py job.json [--outdir DIR] [--port 9222]

job.json 格式:
{
  "outdir": "C:\\\\Users\\\\me\\\\Desktop\\\\文献",
  "items": [
    {"file": "01_xxx_MSEA2019.pdf", "doi": "10.1016/j.msea.2018.09.055"},
    {"file": "02_yyy_MMI2018.pdf",  "doi": "10.1007/s12540-018-0030-x"}
  ]
}

依赖: pip install websocket-client
"""
import argparse
import json
import os
import re
import shutil
import sys
import time
import urllib.request

try:
    import websocket
except ImportError:
    sys.exit("缺少依赖，请先运行: python -m pip install websocket-client")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEFAULT_DOWNLOADS = os.path.join(os.path.expanduser("~"), "Downloads")


# --------------------------------------------------------------------------
# 极简 CDP 客户端
# --------------------------------------------------------------------------
def _http_json(port, path, timeout=15):
    with urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, path), timeout=timeout) as r:
        return json.load(r)


class CDP:
    def __init__(self, port=9222, timeout=180):
        v = _http_json(port, "/json/version")
        # 关键: suppress_origin=True，否则新版 Chromium 会以 403 拒绝 WebSocket 握手
        self.ws = websocket.create_connection(
            v["webSocketDebuggerUrl"], timeout=timeout, suppress_origin=True)
        self.ws.settimeout(2.0)
        self.port = port
        self._id = 0

    def cmd(self, method, params=None, session=None):
        self._id += 1
        msg = {"id": self._id, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        self.ws.send(json.dumps(msg))
        deadline = time.time() + 180
        while time.time() < deadline:
            try:
                raw = self.ws.recv()
            except Exception:
                continue
            if not raw:
                continue
            d = json.loads(raw)
            if "id" in d and d["id"] == self._id:
                return d
        return {"error": "timeout"}

    # --- 标签页管理 ---
    def targets(self):
        return self.cmd("Target.getTargets").get("result", {}).get("targetInfos", [])

    def page(self):
        """优先返回 http(s) 页面，避开 edge:// / chrome:// 内部页。"""
        pages = [t for t in self.targets() if t.get("type") == "page"]
        for t in pages:
            if str(t.get("url", "")).startswith("http"):
                return t
        return pages[0] if pages else None

    def close_internal_pages(self):
        for t in self.targets():
            u = str(t.get("url", ""))
            if t.get("type") == "page" and (u.startswith("edge://") or u.startswith("chrome://")):
                self.cmd("Target.closeTarget", {"targetId": t["targetId"]})

    def session(self):
        return self.cmd("Target.attachToTarget",
                        {"targetId": self.page()["targetId"], "flatten": True})["result"]["sessionId"]

    def url(self):
        return self.page().get("url", "")

    def title(self):
        return self.page().get("title", "")

    # --- 导航与求值 ---
    def goto(self, url, wait=8):
        s = self.session()
        self.cmd("Page.enable", session=s)
        self.cmd("Page.navigate", {"url": url}, session=s)
        time.sleep(wait)

    def js(self, expr, await_promise=True):
        s = self.session()
        r = self.cmd("Runtime.evaluate",
                     {"expression": expr, "returnByValue": True, "awaitPromise": await_promise},
                     session=s)
        res = r.get("result", {})
        if "exceptionDetails" in res:
            return {"__error__": str(res["exceptionDetails"].get("exception", {}).get("description"))[:300]}
        return res.get("result", {}).get("value")

    def set_download_dir(self, path):
        return self.cmd("Browser.setDownloadBehavior",
                        {"behavior": "allow", "downloadPath": path, "eventsEnabled": True})


# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------
def host_of(url):
    m = re.match(r"https?://([^/]+)", url or "")
    return (m.group(1) if m else "").lower()


def find_new_file(since, dirs):
    best = None
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            p = os.path.join(d, f)
            try:
                mt, sz = os.path.getmtime(p), os.path.getsize(p)
            except OSError:
                continue
            if mt > since and sz > 0 and not f.endswith(".crdownload"):
                if best is None or mt > best[0]:
                    best = (mt, p, sz)
    return best


def is_pdf(path):
    try:
        with open(path, "rb") as f:
            return f.read(5) == b"%PDF-"
    except OSError:
        return False


FIND_PDF_LINK = r"""
(function(){
  var out = [];
  document.querySelectorAll('a').forEach(function(a){
    var h = a.getAttribute('href') || '';
    if (h.indexOf('pdfft') >= 0 || /\.pdf(\?|$)/i.test(h)) out.push(h);
  });
  var t = document.body ? (document.body.innerText || '') : '';
  return JSON.stringify({
    pdf: out.slice(0, 5),
    url: location.href,
    hasInst: /Brought to you by/i.test(t),
    instLine: (t.match(/Brought to you by[^\n]{0,80}/i) || [''])[0],
    paywall: /Buy article PDF|Purchase PDF|Get Access|Rent this article/i.test(t)
  });
})()
"""


def poll_pdf_meta(c, timeout=45, interval=3):
    """轮询文章页直到 PDF 链接出现。

    出版商的文章页多是重度 SPA，固定 sleep 不可靠（首次导航尤其慢）。
    必须轮询到 <a> 数量稳定且出现 pdf 链接为止。
    """
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        raw = c.js(FIND_PDF_LINK)
        if isinstance(raw, str):
            try:
                meta = json.loads(raw)
            except Exception:
                meta = {}
        elif isinstance(raw, dict):
            meta = raw
        else:
            meta = {}
        if meta.get("__error__"):
            meta = {}
        last = meta
        if meta.get("pdf"):
            return meta
        time.sleep(interval)
    return last


def wait_for_signed_url(c, timeout=30, interval=2):
    """导航到 pdfft 后，轮询直到 URL 变成签名直链（或确认被弹回文章页）。"""
    deadline = time.time() + timeout
    url = c.url()
    while time.time() < deadline:
        url = c.url()
        base = url.lower().split("?")[0]
        if "sciencedirectassets" in url or base.endswith(".pdf"):
            return url
        if "/article/" in url and time.time() > deadline - timeout + 6:
            # 等一会儿仍停在文章页 => 无权限
            time.sleep(interval)
            url = c.url()
            if "/article/" in url:
                return url
        time.sleep(interval)
    return url


def blob_download_js(url, filename):
    return r"""
(async function(){
  try{
    var r = await fetch(%s, {credentials:'include'});
    if(!r.ok) return JSON.stringify({err:'HTTP '+r.status});
    var b = await r.blob();
    if(b.size < 20000) return JSON.stringify({err:'too small', size:b.size, type:b.type});
    var a = document.createElement('a');
    a.href = URL.createObjectURL(b);
    a.download = %s;
    document.body.appendChild(a);
    a.click();
    return JSON.stringify({ok:true, size:b.size, type:b.type});
  }catch(e){ return JSON.stringify({err:String(e)}); }
})()
""" % (json.dumps(url), json.dumps(filename))


# --------------------------------------------------------------------------
# 核心：按出版商标记选择策略
# --------------------------------------------------------------------------
def try_sciencedirect(c, doi, filename, outdir, downloads):
    """ScienceDirect 两步法。

    必须两步，原因：ScienceDirect 对程序化 fetch 有反爬拦截，直接 fetch 文章页或
    pdfft 链接都会返回 HTML 挑战页。但先用真实页面「导航」到 pdfft，服务器会 302
    到带 AWS 签名的 S3 直链 (pdf.sciencedirectassets.com/...main.pdf?X-Amz-...)。
    该直链可被同源/跨源 fetch 正常取回 —— 于是：①导航取签名直链 ②回文章页 fetch 它。
    """
    t0 = time.time()
    c.goto("https://doi.org/" + doi, 6)
    art_url = c.url()
    meta = poll_pdf_meta(c, timeout=45)
    if meta.get("instLine"):
        print("     机构授权: %s" % meta["instLine"][:80])
    elif meta.get("paywall"):
        print("     ⚠ 页面显示付费墙")

    links = meta.get("pdf") or []
    pdf_link = None
    for h in links:
        if "pdfft" in h:
            pdf_link = h
            break
    if not pdf_link and links:
        pdf_link = links[0]
    if not pdf_link:
        return False, "文章页未找到 PDF 链接（可能无权限或页面结构变化）"
    if pdf_link.startswith("/"):
        pdf_link = "https://www.sciencedirect.com" + pdf_link

    # 步骤 1：导航取得签名直链
    c.goto(pdf_link, 6)
    signed = wait_for_signed_url(c, timeout=30)
    if "pdf.sciencedirectassets.com" not in signed and not signed.lower().split("?")[0].endswith(".pdf"):
        return False, "未取得签名直链（落地: %s）" % signed[:80]

    # 步骤 2：回文章页，同源方案 fetch 签名直链
    c.goto(art_url, 8)
    res = c.js(blob_download_js(signed, filename))
    print("     fetch: %s" % str(res)[:110])
    time.sleep(6)

    got = find_new_file(t0, [outdir, downloads])
    if not got:
        return False, "文件未落盘"
    _, path, size = got
    if not is_pdf(path):
        return False, "落盘文件不是 PDF"
    dest = os.path.join(outdir, filename)
    if os.path.abspath(path) != os.path.abspath(dest):
        shutil.move(path, dest)
    return True, "%.2f MB" % (size / 1048576.0)


def try_springer(c, doi, filename, outdir, downloads):
    """SpringerLink：PDF 直链规律为 /content/pdf/<DOI>.pdf。

    若机构走「IP 授权」（常见于 DRAA 联盟采购），即使 CARSI/Shibboleth 联邦流程
    全部返回 200，content/pdf 仍会被 302 回文章页 —— 此时视为无权限。
    """
    t0 = time.time()
    art_url = "https://link.springer.com/article/" + doi
    pdf_url = "https://link.springer.com/content/pdf/%s.pdf" % doi
    c.goto(pdf_url, 11)
    final = c.url()
    if "/article/" in final:
        return False, "content/pdf 被重定向回文章页 = 该刊不在机构订阅内"
    c.goto(art_url, 8)
    res = c.js(blob_download_js(pdf_url, filename))
    print("     fetch: %s" % str(res)[:110])
    time.sleep(6)
    got = find_new_file(t0, [outdir, downloads])
    if not got:
        return False, "文件未落盘"
    _, path, size = got
    if not is_pdf(path):
        return False, "落盘文件不是 PDF"
    dest = os.path.join(outdir, filename)
    if os.path.abspath(path) != os.path.abspath(dest):
        shutil.move(path, dest)
    return True, "%.2f MB" % (size / 1048576.0)


def try_generic(c, doi, filename, outdir, downloads):
    """通用兜底：打开 DOI 落地页，找任意 .pdf 链接后 fetch 下载。"""
    t0 = time.time()
    c.goto("https://doi.org/" + doi, 6)
    meta = poll_pdf_meta(c, timeout=40)
    links = meta.get("pdf") or []
    if not links:
        return False, "未找到 PDF 链接"
    url = links[0]
    if url.startswith("/"):
        url = "https://" + host_of(c.url()) + url
    res = c.js(blob_download_js(url, filename))
    print("     fetch: %s" % str(res)[:110])
    time.sleep(6)
    got = find_new_file(t0, [outdir, downloads])
    if not got:
        return False, "文件未落盘"
    _, path, size = got
    if not is_pdf(path):
        return False, "落盘文件不是 PDF"
    dest = os.path.join(outdir, filename)
    if os.path.abspath(path) != os.path.abspath(dest):
        shutil.move(path, dest)
    return True, "%.2f MB" % (size / 1048576.0)


ROUTES = [
    ("sciencedirect.com", try_sciencedirect),
    ("springer.com", try_springer),
    ("springernature.com", try_springer),
]


def dispatch(c, doi, filename, outdir, downloads):
    """先看 DOI 会落到哪个出版商域名，再选策略。"""
    c.goto("https://doi.org/" + doi, 8)
    landing = c.url()
    h = host_of(landing)
    print("     落地: %s" % landing[:100])
    for key, fn in ROUTES:
        if key in h:
            return fn(c, doi, filename, outdir, downloads)
    return try_generic(c, doi, filename, outdir, downloads)


# --------------------------------------------------------------------------
def check_access(c, outdir=None, downloads=None):
    """探测主要出版商当前的机构授权状态。

    判定必须基于**正面证据**：页面出现 `Brought to you by: <机构>` 或能取到 PDF 链接
    才算 ✓。仅凭"没看到付费墙文案"是假阳性（SPA 未渲染时会两者皆空）。
    """
    PROBES = [
        ("ScienceDirect", "10.1016/j.msea.2018.09.055"),
        ("SpringerLink", "10.1007/s12540-018-0030-x"),
    ]
    print("=== 机构授权探测 ===")
    for name, doi in PROBES:
        c.goto("https://doi.org/" + doi, 6)
        meta = poll_pdf_meta(c, timeout=40)
        inst = meta.get("instLine") or ""
        has_pdf = bool(meta.get("pdf"))
        paywall = bool(meta.get("paywall"))
        ok = bool(inst) or has_pdf
        print("  [%s] %s" % ("✓" if ok else "✗", name))
        if inst:
            print("      正面证据: %s" % inst[:90])
        elif has_pdf:
            print("      正面证据: 找到 PDF 链接 %s" % str(meta['pdf'][0])[:80])
        else:
            print("      无正面证据（未渲染出机构标识或 PDF 链接）")
        if paywall and not ok:
            print("      检测到付费墙文案")
    print("\n若某出版商为 ✗：可能订阅走 IP 授权而非联邦认证（需校园网/VPN），"
          "或该刊不在订阅范围。这类文献视为拿不到，如实记入失败清单即可。")


def main():
    ap = argparse.ArgumentParser(description="通过真实浏览器批量下载文献 PDF")
    ap.add_argument("job", nargs="?", help="任务 JSON 文件")
    ap.add_argument("--outdir", default=None, help="输出目录（覆盖 job 内设置）")
    ap.add_argument("--port", type=int, default=9222, help="CDP 端口")
    ap.add_argument("--check", action="store_true", help="仅探测机构授权状态")
    ap.add_argument("--force", action="store_true", help="已存在也重新下载")
    args = ap.parse_args()

    c = CDP(port=args.port)
    c.close_internal_pages()
    time.sleep(1)

    if args.check:
        check_access(c, None, None)
        return

    if not args.job:
        ap.error("需要 job.json，或使用 --check")

    with open(args.job, "r", encoding="utf-8") as f:
        job = json.load(f)

    outdir = args.outdir or job.get("outdir") or os.getcwd()
    items = job.get("items") or []
    os.makedirs(outdir, exist_ok=True)
    downloads = job.get("downloadsDir") or DEFAULT_DOWNLOADS

    print("输出目录: %s" % outdir)
    print("共 %d 篇\n" % len(items))
    c.set_download_dir(outdir)

    ok, bad, skip = [], [], []
    for it in items:
        fn = it.get("file") or (re.sub(r"[^\w.\-]+", "_", it.get("doi", "paper")) + ".pdf")
        doi = it.get("doi")
        print("=" * 68)
        print("### %s\n     %s" % (fn, doi))
        dest = os.path.join(outdir, fn)
        if not args.force and is_pdf(dest):
            print("     已存在且为 PDF，跳过")
            skip.append(fn)
            continue
        try:
            good, msg = dispatch(c, doi, fn, outdir, downloads)
        except Exception as e:
            good, msg = False, "异常: %s" % str(e)[:120]
        print("     %s %s" % ("✓" if good else "✗", msg))
        (ok if good else bad).append(fn)

    print("\n" + "=" * 68)
    print("成功 %d ｜ 跳过 %d ｜ 失败 %d" % (len(ok), len(skip), len(bad)))
    for f in ok:
        print("   ✓", f)
    for f in bad:
        print("   ✗", f)
    if bad:
        print("\n失败项见上方 ✗ 列表；报告里需注明失败原因（无权限 / 反爬拦截 / CAPTCHA 待人工）。")


if __name__ == "__main__":
    main()
