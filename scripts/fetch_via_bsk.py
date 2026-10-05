#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""备选方案 B：通过 BrowserSkill（bsk CLI）批量下载文献 PDF。

为什么需要它：
  - 主方案（fetch.py + 裸 CDP）用独立 profile，用户需要重新登录一次。
  - BrowserSkill 复用**用户真实浏览器里已有的登录态**，且内置人工介入交接。

已知限制（实测 v0.3.1）：
  1. `bsk download --ref` 对「内联 PDF 链接」会报 download_capture_failed，
     所以本脚本改为：`bsk click --ref` 触发原生下载 → 轮询系统下载目录捞文件。
  2. `bsk evaluate` 里 `.blob()` 会让 RPC 超时（但 JS 其实会跑完），
     因此降级策略是「发出去就别等，直接轮询下载目录」。
  3. PowerShell 里 `@eN` 会被 splatting 吃掉，必须写成 `--ref '@eN'`。
     本脚本用 subprocess 列表传参，不受此影响。

用法:
  python fetch_via_bsk.py job.json [--outdir DIR] [--check]

前置:
  1. 启动 daemon（需持久任务，见 bsk_setup.ps1）
  2. 浏览器已装 BrowserSkill 扩展并 connected（bsk browsers 有输出）
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEFAULT_DOWNLOADS = os.path.join(os.path.expanduser("~"), "Downloads")


# --------------------------------------------------------------------------
def find_bsk():
    """定位 bsk 可执行文件。"""
    cands = [
        os.path.expanduser(r"~\.local\bin\bsk.exe"),
        os.path.expanduser(r"~/.local/bin/bsk"),
        shutil.which("bsk") or "",
    ]
    for c in cands:
        if c and os.path.exists(c):
            return c
    return "bsk"


BSK = find_bsk()


def run(args, timeout=120, env_extra=None):
    """执行 bsk 命令，返回 (returncode, stdout+stderr)。"""
    env = dict(os.environ)
    env["BSK_AUTO_START"] = "0"          # 沙箱里不要让它自动拉 daemon
    if env_extra:
        env.update(env_extra)
    try:
        p = subprocess.run([BSK] + args, capture_output=True, timeout=timeout, env=env)
        out = (p.stdout or b"").decode("utf-8", "replace") + (p.stderr or b"").decode("utf-8", "replace")
        return p.returncode, out
    except subprocess.TimeoutExpired:
        return 124, "__TIMEOUT__"
    except Exception as e:
        return 125, str(e)


def run_json(args, timeout=120):
    rc, out = run(args + ["--json"], timeout=timeout)
    try:
        return rc, json.loads(out)
    except Exception:
        return rc, {"__raw__": out[:400]}


def preflight():
    """检查 daemon 与浏览器连接。"""
    print("bsk:", BSK)
    rc, ver = run(["--version"], timeout=30)
    print("  版本:", ver.strip() or "(无输出)")
    if rc != 0:
        return False
    rc, st = run_json(["status"], timeout=30)
    bs = (st or {}).get("browsers") or []
    if not bs:
        print("  ✗ 没有已连接的浏览器。请确认扩展已安装并连接到 daemon。")
        return False
    b = bs[0]
    print("  ✓ 浏览器已连接: %s %s (扩展 %s)" % (b.get("browser_name"), b.get("browser_version"),
                                                 b.get("extension_version")))
    return True


def new_session():
    rc, d = run_json(["session", "start", "--no-focus"], timeout=60)
    sid = (d or {}).get("session_id")
    if not sid:
        raise RuntimeError("session start 失败: %s" % str(d)[:200])
    return sid


def stop_all():
    run(["session", "stop", "--all"], timeout=60)


# --------------------------------------------------------------------------
FIND_PDF_JS = (
    "JSON.stringify({"
    "n:document.querySelectorAll('a').length,"
    "inst:(document.body.innerText.match(/Brought to you by[^\\n]{0,60}/)||[''])[0],"
    "pdfs:Array.from(document.querySelectorAll('a'))"
    ".filter(a=>/pdfft|\\/pdf\\/|\\.pdf($|\\?)/i.test(a.href))"
    ".map(a=>({t:(a.textContent||'').trim().slice(0,30),h:a.href})).slice(0,6),"
    "paywall:/Buy article PDF|Purchase PDF|Get Access|Rent this article/i.test(document.body.innerText)"
    "})"
)


def poll_pdf_meta(sid, timeout=45, interval=4):
    """轮询直到文章页渲染出 PDF 链接（SPA 首次加载很慢，不能固定 sleep）。"""
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        rc, d = run_json(["evaluate", FIND_PDF_JS, "--session", sid, "--timeout", "30s"], timeout=60)
        val = (d or {}).get("value")
        if isinstance(val, str):
            try:
                last = json.loads(val)
            except Exception:
                last = {}
        elif isinstance(val, dict):
            last = val
        if last.get("pdfs"):
            return last
        time.sleep(interval)
    return last


def snapshot_pdf_ref(sid):
    """从 aria 快照里找 PDF 链接的 @eN 引用。"""
    rc, d = run_json(["snapshot", "--session", sid], timeout=60)
    text = (d or {}).get("text") or ""
    # 优先匹配明确的 PDF 链接文字
    for pat in (r'(@e\d+)\s+link\s+"([^"]*(?:View PDF|Download PDF|PDF)[^"]*)"',
                r'(@e\d+)\s+link\s+"([^"]*[Pp][Dd][Ff][^"]*)"'):
        m = re.search(pat, text)
        if m:
            return m.group(1), m.group(2)
    return None, None


def find_new_file(since, dirs, exts=(".pdf",)):
    """在候选目录里找 since 之后新增的 PDF。"""
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
            if mt > since and sz > 0 and f.lower().endswith(exts):
                if best is None or mt > best[0]:
                    best = (mt, p, sz)
    return best


def wait_for_download(since, dirs, timeout=90):
    """轮询等待下载完成（忽略 .crdownload 中间态）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        got = find_new_file(since, dirs)
        if got:
            mt, p, sz = got
            # 若仍是 .crdownload 说明未完成
            if not p.lower().endswith(".crdownload"):
                time.sleep(2)          # 再等一下确保写盘结束
                return (mt, p, os.path.getsize(p))
        time.sleep(3)
    return None


def is_pdf(path):
    try:
        with open(path, "rb") as f:
            return f.read(5) == b"%PDF-"
    except OSError:
        return False


# --------------------------------------------------------------------------
def grab(c, doi, filename, outdir, downloads):
    """下载单篇。返回 (ok, msg)。"""
    t0 = time.time()
    rc, nav = run_json(["navigate", "https://doi.org/" + doi,
                        "--session", c, "--wait-until", "domcontentloaded", "--timeout", "60s"], timeout=120)
    print("     落地:", str((nav or {}).get("final_url", ""))[:100])

    meta = poll_pdf_meta(c, timeout=45)
    if meta.get("inst"):
        print("     机构授权:", meta["inst"][:80])
    elif meta.get("paywall"):
        print("     ⚠ 检测到付费墙文案")

    if not meta.get("pdfs"):
        return False, "文章页未渲染出 PDF 链接（无权限 / 反爬 / SPA 未完成）"

    # 策略 A：点击 PDF 链接触发原生下载（需浏览器已设为「PDF 直接下载」）
    ref, label = snapshot_pdf_ref(c)
    if ref:
        print("     点击 %s (\"%s\")" % (ref, label))
        run_json(["click", "--ref", ref, "--session", c], timeout=60)
        got = wait_for_download(t0, [downloads, outdir], timeout=75)
        if got and is_pdf(got[1]):
            _, path, sz = got
            dest = os.path.join(outdir, filename)
            if os.path.abspath(path) != os.path.abspath(dest):
                shutil.move(path, dest)
            return True, "%.2f MB（原生下载）" % (sz / 1048576.0)
        print("     ↳ 原生下载未捕获，降级到 evaluate/blob")

    # 策略 B：页面内 fetch + blob + <a download>
    #   注意：bsk evaluate 的 RPC 会超时，但 JS 仍会跑完 → 发出去后只轮询下载目录
    href = meta["pdfs"][0]["h"]
    js = (
        "(function(){var u=%s;fetch(u,{credentials:'include'})"
        ".then(function(r){return r.blob()})"
        ".then(function(b){var a=document.createElement('a');"
        "a.href=URL.createObjectURL(b);a.download=%s;"
        "document.body.appendChild(a);a.click();});return 'fired';})()"
        % (json.dumps(href), json.dumps(filename))
    )
    run_json(["evaluate", js, "--session", c, "--await-promise", "false", "--timeout", "20s"], timeout=40)
    got = wait_for_download(t0, [downloads, outdir], timeout=120)
    if got and is_pdf(got[1]):
        _, path, sz = got
        dest = os.path.join(outdir, filename)
        if os.path.abspath(path) != os.path.abspath(dest):
            shutil.move(path, dest)
        return True, "%.2f MB（blob 下载）" % (sz / 1048576.0)
    return False, "两种策略均未拿到文件"


def main():
    ap = argparse.ArgumentParser(description="通过 BrowserSkill 批量下载文献 PDF")
    ap.add_argument("job", nargs="?", help="任务 JSON（同 fetch.py 格式）")
    ap.add_argument("--outdir", default=None)
    ap.add_argument("--check", action="store_true", help="仅检查 daemon/扩展连接状态")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    if not preflight():
        sys.exit(2)
    if args.check:
        return
    if not args.job:
        ap.error("需要 job.json，或使用 --check")

    with open(args.job, "r", encoding="utf-8") as f:
        job = json.load(f)
    outdir = args.outdir or job.get("outdir") or os.getcwd()
    items = job.get("items") or []
    downloads = job.get("downloadsDir") or DEFAULT_DOWNLOADS
    os.makedirs(outdir, exist_ok=True)

    print("输出目录:", outdir)
    print("下载监听目录:", downloads)
    print("共 %d 篇\n" % len(items))

    sid = new_session()
    print("会话:", sid, "\n")
    ok, bad, skip = [], [], []
    try:
        for it in items:
            fn = it.get("file") or (re.sub(r"[^\w.\-]+", "_", it.get("doi", "paper")) + ".pdf")
            doi = it.get("doi")
            print("=" * 66)
            print("### %s\n     %s" % (fn, doi))
            dest = os.path.join(outdir, fn)
            if not args.force and is_pdf(dest):
                print("     已存在且为 PDF，跳过")
                skip.append(fn)
                continue
            try:
                good, msg = grab(sid, doi, fn, outdir, downloads)
            except Exception as e:
                good, msg = False, "异常: %s" % str(e)[:120]
            print("     %s %s" % ("✓" if good else "✗", msg))
            (ok if good else bad).append(fn)
    finally:
        stop_all()

    print("\n" + "=" * 66)
    print("成功 %d ｜ 跳过 %d ｜ 失败 %d" % (len(ok), len(skip), len(bad)))
    for f in ok:
        print("   ✓", f)
    for f in bad:
        print("   ✗", f)


if __name__ == "__main__":
    main()
