#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""校验下载目录里的 PDF：文件头、页数、首页文本，并输出清单。

用法:
  python verify.py <目录> [--json out.json]
依赖: pip install pypdf
"""
import argparse
import glob
import json
import logging
import os
import re
import sys

# pypdf 在缺 fontTools 时会对每个 CFF 字体刷告警，压掉以免污染输出
logging.getLogger("pypdf").setLevel(logging.ERROR)
logging.getLogger("pypdf._cmap").setLevel(logging.ERROR)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def head5(path):
    try:
        with open(path, "rb") as f:
            return f.read(5)
    except OSError:
        return b""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("directory")
    ap.add_argument("--json", dest="json_out", default=None)
    args = ap.parse_args()

    try:
        from pypdf import PdfReader
    except ImportError:
        sys.exit("缺少依赖，请先运行: python -m pip install pypdf")

    files = sorted(glob.glob(os.path.join(args.directory, "*.pdf")))
    if not files:
        sys.exit("目录内没有 PDF: %s" % args.directory)

    rows, bad, total = [], 0, 0.0
    print("=" * 78)
    for f in files:
        name = os.path.basename(f)
        size_mb = os.path.getsize(f) / 1048576.0
        total += size_mb
        rec = {"file": name, "mb": round(size_mb, 2)}
        if head5(f) != b"%PDF-":
            rec["error"] = "非 PDF 文件头"
            bad += 1
            rows.append(rec)
            print("%-58s  ✗ 文件头异常" % name[:58])
            continue
        try:
            r = PdfReader(f)
            pages = len(r.pages)
            txt = re.sub(r"\s+", " ", (r.pages[0].extract_text() or ""))[:110]
            rec.update({"pages": pages, "first_text": txt})
            print("%-58s %3dp %7.2fMB" % (name[:58], pages, size_mb))
            if txt:
                print("      %s" % txt)
        except Exception as e:
            rec["error"] = str(e)[:120]
            bad += 1
            print("%-58s  ✗ 解析失败: %s" % (name[:58], str(e)[:60]))
        rows.append(rec)

    print("=" * 78)
    print("共 %d 份，合计 %.1f MB，异常 %d 份" % (len(files), total, bad))
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False, indent=1)
        print("清单已写入 %s" % args.json_out)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
