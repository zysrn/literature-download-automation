#!/usr/bin/env python3
"""仓库自检：发布/提交前跑一遍，确认没有踩到本项目已知的坑。

用法:
    python scripts/selfcheck.py

检查项:
  1. SKILL.md frontmatter 能被 YAML 正确解析（未加引号的值里不能有 ": "）
  2. 含中文的 .ps1 必须带 UTF-8 BOM（否则 PowerShell 5.1 按 GBK 解码报错）
  3. 所有文本文件是合法 UTF-8
  4. 没有误提交 __pycache__ / *.pyc / PDF / job.json
  5. 没有明显的凭据泄漏
"""

import re
import sys
from pathlib import Path

# Windows 控制台默认可能是 cp1252/GBK，直接 print 中文会抛 UnicodeEncodeError
# （GitHub Actions 的 windows-latest runner 就是这种情况）。
# 强制 stdout/stderr 使用 UTF-8，无法编码的字符用替代符而不是崩溃。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError, ValueError):  # Python < 3.7 或非标准流
        pass

ROOT = Path(__file__).resolve().parent.parent

OK = "  [ok]  "
WARN = "  [warn]"
FAIL = "  [FAIL]"

problems: list[str] = []
warnings: list[str] = []


def rel(p: Path) -> str:
    return str(p.relative_to(ROOT)).replace("\\", "/")


def check_frontmatter() -> None:
    print("[1] SKILL.md frontmatter")
    skill = ROOT / "SKILL.md"
    if not skill.exists():
        problems.append("SKILL.md 不存在")
        print(FAIL + " SKILL.md 不存在")
        return

    text = skill.read_text(encoding="utf-8")
    m = re.match(r"^---\r?\n(.*?)\r?\n---", text, re.S)
    if not m:
        problems.append("SKILL.md 没有合法的 --- frontmatter --- 区块")
        print(FAIL + " 找不到 frontmatter 区块")
        return

    fm = m.group(1)
    try:
        import yaml
    except ImportError:
        keys = re.findall(r"^(\w+):", fm, re.M)
        print(WARN + f" 未安装 pyyaml，仅做正则检查；找到键: {keys}")
        warnings.append("未安装 pyyaml，frontmatter 未做真正的 YAML 解析")
        expected = {"name", "description", "whenToUse"}
        missing = expected - set(keys)
        if missing:
            problems.append(f"frontmatter 缺少键: {sorted(missing)}")
            print(FAIL + f" 缺少键: {sorted(missing)}")
        return

    try:
        data = yaml.safe_load(fm)
    except Exception as exc:  # noqa: BLE001
        problems.append(f"frontmatter YAML 解析失败: {exc}")
        print(FAIL + f" YAML 解析失败: {exc}")
        return

    if not isinstance(data, dict):
        problems.append("frontmatter 解析结果不是映射（多半是未加引号的值里有 ': '）")
        print(FAIL + " 解析结果不是 dict —— 检查未加引号值里的 ': '")
        return

    expected = {"name", "description", "whenToUse"}
    missing = expected - set(data)
    if missing:
        problems.append(f"frontmatter 缺少键: {sorted(missing)}")
        print(FAIL + f" 缺少键: {sorted(missing)}")
        return

    print(OK + f" YAML 解析成功，键: {sorted(data)}")
    print(f"        name      = {data['name']}")
    print(f"        desc len  = {len(str(data['description']))}")

    # 值必须是标量字符串：若是 dict/list 说明被 ': ' 拆成了嵌套结构
    for k in expected:
        if not isinstance(data[k], str):
            problems.append(f"frontmatter 的 {k} 不是字符串（被 ': ' 解析成了嵌套结构？）")
            print(FAIL + f" {k} 不是字符串，实际是 {type(data[k]).__name__}")


def check_ps1_bom() -> None:
    print("[2] .ps1 的 UTF-8 BOM")
    files = sorted((ROOT / "scripts").glob("*.ps1"))
    if not files:
        print(WARN + " scripts/ 下没有 .ps1")
        return
    for f in files:
        raw = f.read_bytes()
        has_bom = raw[:3] == b"\xef\xbb\xbf"
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            problems.append(f"{rel(f)} 不是合法 UTF-8: {exc}")
            print(FAIL + f" {rel(f)} 不是合法 UTF-8")
            continue
        has_cjk = bool(re.search(r"[\u4e00-\u9fff]", text))
        if has_cjk and not has_bom:
            problems.append(f"{rel(f)} 含中文但缺少 UTF-8 BOM（PowerShell 5.1 会解码失败）")
            print(FAIL + f" {rel(f)} 含中文但无 BOM")
        else:
            note = "含中文，BOM 正常" if has_cjk else "无中文，无需 BOM"
            print(OK + f" {rel(f)} ({note})")


def check_utf8() -> None:
    print("[3] 文本文件 UTF-8 合法性")
    exts = {".md", ".py", ".ps1", ".json", ".yml", ".yaml", ".txt", ".cfg", ".toml"}
    bad = 0
    for p in ROOT.rglob("*"):
        if not p.is_file() or ".git" in p.parts or p.suffix.lower() not in exts:
            continue
        try:
            p.read_bytes().decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            problems.append(f"{rel(p)} 不是合法 UTF-8: {exc}")
            print(FAIL + f" {rel(p)}")
            bad += 1
    if bad == 0:
        print(OK + " 全部文本文件均为合法 UTF-8")


def check_junk() -> None:
    print("[4] 不该提交的垃圾文件")
    bad_patterns = ["__pycache__", ".pyc", ".crdownload"]
    found = []
    for p in ROOT.rglob("*"):
        if ".git" in p.parts:
            continue
        s = str(p).lower()
        if any(b in s for b in bad_patterns) or p.suffix.lower() == ".pdf":
            found.append(rel(p))
    if found:
        for f in found:
            warnings.append(f"仓库里存在不该提交的文件: {f}")
            print(WARN + f" {f}")
    else:
        print(OK + " 无 __pycache__ / *.pyc / *.pdf")

    if (ROOT / "job.json").exists():
        warnings.append("仓库里存在 job.json（含个人下载清单，建议只提交 examples/job.example.json）")
        print(WARN + " 存在 job.json —— 建议改用 examples/job.example.json")
    else:
        print(OK + " 无 job.json")


def check_secrets() -> None:
    print("[5] 凭据泄漏扫描")
    patterns = [
        (r"ghp_[A-Za-z0-9]{20,}", "GitHub PAT"),
        (r"github_pat_[A-Za-z0-9_]{20,}", "GitHub fine-grained PAT"),
        (r"sk-[A-Za-z0-9]{20,}", "OpenAI 风格 key"),
        (r"AKIA[0-9A-Z]{16}", "AWS Access Key"),
        (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "私钥"),
        (r"(?i)\b(password|passwd|token|api[_-]?key)\s*[:=]\s*['\"][^'\"]{6,}['\"]", "硬编码凭据"),
    ]
    hits = []
    for p in ROOT.rglob("*"):
        if not p.is_file() or ".git" in p.parts:
            continue
        if p.suffix.lower() in {".pdf", ".png", ".jpg", ".zip", ".exe", ".dll", ".db"}:
            continue
        try:
            text = p.read_text(encoding="utf-8-sig", errors="ignore")
        except OSError:
            continue
        for pat, label in patterns:
            for m in re.finditer(pat, text):
                line_no = text[: m.start()].count("\n") + 1
                hits.append(f"{rel(p)}:{line_no} 疑似 {label}")
    if hits:
        for h in hits:
            problems.append(f"疑似凭据泄漏: {h}")
            print(FAIL + f" {h}")
    else:
        print(OK + " 未发现明显凭据")


def main() -> int:
    print(f"仓库自检: {ROOT}\n")
    check_frontmatter()
    check_ps1_bom()
    check_utf8()
    check_junk()
    check_secrets()

    print("\n" + "=" * 60)
    if problems:
        print(f"结果: 失败 —— {len(problems)} 个必须修复的问题")
        for p in problems:
            print(f"  - {p}")
    elif warnings:
        print(f"结果: 通过（有 {len(warnings)} 条提醒）")
        for w in warnings:
            print(f"  - {w}")
    else:
        print("结果: 全部通过 ✅")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
