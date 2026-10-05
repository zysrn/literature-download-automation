# literature-download-automation

> 一个给 AI Agent 用的**文献 PDF 批量下载** Skill：走**合法渠道**（开放获取 OA + 你所在机构的订阅），
> 自动从 DOI 列表抓到本地 PDF，并把每一篇的结果**如实记账**。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-blue.svg)](#4-平台差异)
[![Skill](https://img.shields.io/badge/DSH-Skill-8A2BE2.svg)](#2-安装)

这是一个 **DSH 文件系统 Skill**（`SKILL.md` + `scripts/`）。搬运时**整个文件夹拷过去**即可，无需改配置、无需注册。

内含**两套可互换的下载驱动**：

| | 方案 A（默认） | 方案 B（备选） |
|---|---|---|
| 机制 | 裸 CDP + `fetch.py` | Tencent BrowserSkill（`bsk` CLI） |
| 依赖 | `websocket-client`、`pypdf` | 无需额外 pip 包 |
| 特点 | 零依赖、下载路径最可控 | 复用真实浏览器登录态，内置人工介入交接 |

---

## 1. 它能做什么 / 不做什么

### ✅ 会做

- 从 **DOI 列表**出发，先探测**开放获取**（Unpaywall / OpenAlex / Semantic Scholar），能白拿的直接拿
- 白拿不到时，走**你所在机构的订阅**（如 CARSI 联邦登录），在你**已登录**的浏览器里完成下载
- 批量落盘 PDF，并用 `verify.py` 校验文件是不是**真 PDF**、有没有下漏
- 输出**逐条结果**（成功 / 失败 / 原因），不美化、不糊弄

### ❌ 不会做（这是设计原则，不是能力缺失）

- 不请求、不接触、不存储你的**任何密码**——登录动作由**你本人**在浏览器里完成
- 不绕过付费墙，不自动破解 / 识别验证码
- **只服务个人科研与教学用途**；请遵守出版商条款与你所在机构的订阅协议

> 换句话说：它自动化的是**你已经有权访问**的内容的搬运过程，而不是去获取你本无权限的内容。

---

## 2. 安装

### 方式一：DSH Skill（推荐）

DSH 的 skill 提供者（`@deepseek-ai/dsh-skill-filesystem`）按下列优先级扫描根目录：

| 优先级 | 来源 | 路径 | 适用 |
|---|---|---|---|
| 100 | project-dsh | `<项目根>/.dsh/skills/` | 随仓库走，团队共享 |
| 200 | project-agents | `<项目根>/.agents/skills/` | 同上（AGENTS 约定） |
| 400 | **user-dsh** | **`<DSH_HOME>/skills/`** | **跨项目通用（推荐）** |
| 500 | user-agents | `<agentsHome>/skills/` | — |

> `<项目根>` = 最近的含 `.git` 的祖先目录；`<DSH_HOME>` 默认 `~/.dsh`（Windows: `%USERPROFILE%\.dsh`）。
> 同名 skill 时**优先级数字小的胜出**。

**用户级（推荐）：**

```powershell
# Windows PowerShell
$dst = "$env:USERPROFILE\.dsh\skills"        # 若自定义了 DSH_HOME 请替换
New-Item -ItemType Directory -Path $dst -Force | Out-Null
Copy-Item ".\literature-download-automation" $dst -Recurse -Force
```

```bash
# macOS / Linux
mkdir -p "${DSH_HOME:-$HOME/.dsh}/skills"
cp -R ./literature-download-automation "${DSH_HOME:-$HOME/.dsh}/skills/"
```

**项目级（随 git 仓库共享）：**

```bash
mkdir -p .dsh/skills
cp -R <来源>/literature-download-automation .dsh/skills/
git add .dsh/skills && git commit -m "add literature download skill"
```

**注意事项：**

1. **预设必须是 `standard` 或 `ptc`** —— 这两个预置里挂了 `skill-filesystem` + `tool-skill`。
   **`minimal` 预置不带 skill 支持**，放进去也不会被加载。
2. 无需重启：该提供者**热监听** skill 根目录，新建/改名/删除都会自动进下一次目录。

### 方式二：只用脚本（不装 DSH Skill）

`scripts/` 里的脚本是独立可用的，直接 clone 就能跑：

```bash
git clone https://github.com/<owner>/literature-download-automation.git
cd literature-download-automation
python -m pip install websocket-client pypdf
```

---

## 3. 依赖（仅运行脚本时需要）

### 方案 A（默认）

```bash
python -m pip install websocket-client pypdf
```

- `websocket-client` —— `fetch.py` 与浏览器通信（CDP）
- `pypdf` —— `verify.py` 校验 PDF

### 方案 B（备选，按需）

```powershell
# 一键静默部署 bsk CLI + Edge 扩展
powershell -ExecutionPolicy Bypass -File scripts/bsk_setup.ps1
```

`fetch_via_bsk.py` 只用 Python 标准库（`subprocess` 调 `bsk`），**无需额外 pip 包**。

> **只加载 skill（读 SKILL.md 进目录）不需要任何依赖。**

---

## 4. 平台差异

| 文件 | 平台 | 说明 |
|---|---|---|
| `scripts/launch_edge.ps1` | **仅 Windows** | 方案 A：启动带 `--remote-debugging-port` 的 Edge |
| `scripts/fetch.py` | 跨平台 | 方案 A：纯 Python 批量下载 |
| `scripts/bsk_setup.ps1` | **仅 Windows** | 方案 B：静默部署 BrowserSkill |
| `scripts/fetch_via_bsk.py` | 跨平台 | 方案 B：纯 Python，调 `bsk` CLI |
| `scripts/verify.py` | 跨平台 | PDF 校验 |
| `scripts/selfcheck.py` | 跨平台 | 仓库自检（提交前跑，见下） |

**非 Windows** 上方案 A 等价的手工启动方式：

```bash
# macOS
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --remote-debugging-port=9222 --user-data-dir="$HOME/.dsh-browser-profile" \
  --no-first-run --remote-allow-origins=* "https://ds.carsi.edu.cn/"

# Linux
google-chrome --remote-debugging-port=9222 --user-data-dir="$HOME/.dsh-browser-profile" \
  --no-first-run --remote-allow-origins=* "https://ds.carsi.edu.cn/"
```

---

## 5. 用法

### 5.1 准备任务文件

写一个 `job.json`，把要下的文献列出来（DOI 为准，文件名自定）：

```json
{
  "outdir": "D:\\papers",
  "items": [
    { "file": "vaswani2017-attention.pdf", "doi": "10.48550/arXiv.1706.03762" },
    { "file": "lecun2015-deep-learning.pdf", "doi": "10.1038/nature14539" }
  ]
}
```

### 5.2 方案 A 流程

```powershell
# 1) 启动带调试端口的 Edge，并打开机构登录入口
powershell -ExecutionPolicy Bypass -File scripts\launch_edge.ps1 -Port 9222 -StartUrl "https://ds.carsi.edu.cn/"

# 2) 【人工】在弹出的浏览器里完成机构登录 —— 这一步由你本人操作

# 3) 探测机构授权是否可用
python scripts\fetch.py --check

# 4) 先跑 1 篇验证链路，再批量
python scripts\fetch.py job.json

# 5) 校验落盘结果
python scripts\verify.py D:\papers
```

### 5.3 方案 B 流程

```powershell
powershell -ExecutionPolicy Bypass -File scripts\bsk_setup.ps1   # 部署 CLI + 扩展
bsk daemon start --foreground --daemon-idle 2h                   # 需持久任务/独立终端
bsk browsers                                                     # 应列出 connected 的浏览器
python scripts\fetch_via_bsk.py job.json
```

### 5.4 验证 skill 是否加载成功

在 DSH 会话里：

- 让 agent 调用 `skill` 工具、`name = literature-download-automation`
- 或直接问："你现在有哪些可用 skill？"——目录里应出现该项

### 5.5 仓库自检

改完任何文件后（尤其是 `SKILL.md` 的 frontmatter），跑一遍自检：

```bash
python scripts/selfcheck.py
```

它会检查本项目**踩过的所有坑**：frontmatter 是否被 `: ` 拆坏、`.ps1` 是否有 UTF-8 BOM、
文本是否合法 UTF-8、有没有误提交 PDF/`__pycache__`、有没有凭据泄漏。
每一项都对应下面第 6 节的一条真实事故。

---

## 6. 已知坑（别删 `SKILL.md` 里的排查表）

### Skill 加载相关

- ⚠️ **frontmatter 的 YAML 里不能出现 `: `（冒号+空格）**
  写 `description: ... drivers: a self-contained ...` 会被 YAML 解析成嵌套映射 →
  **整个 skill 被静默丢弃**（目录里直接消失）。中文全角「：」无此问题。
  同类风险：`name`/`description`/`whenToUse` 任何未加引号的值里出现 `: `、`#`、行首 `-`。
  改完 frontmatter 后**务必确认 skill 仍出现在可用列表里**。
- **带中文的 `.ps1` 必须存为 UTF-8 BOM**，否则 PowerShell 5.1 按 GBK 解码会报
  「字符串缺少终止符」（本仓库两个 ps1 都已带 BOM）

### 网络相关

- **网络会间歇性抖动**：实测同一个 GitHub URL 先返回 `000`、几分钟后返回 `200`；
  出口 IP 也会在会话之间变化。**一次失败不要立刻判定为「被墙」**——
  间隔几秒重试 2~3 次再下结论，否则容易误改配置
- **Windows 上 `curl` 报 `CRYPT_E_REVOCATION_OFFLINE`**：这是 schannel 连不上证书吊销
  服务器导致的 TLS 失败，**看起来像被墙，其实网络是通的**。用 `curl --ssl-no-revoke`
  或直接用 `git`（走自己的 TLS 栈）即可验证——排查网络问题前先排除这个可能
- 批量下载时若 `.crdownload` 长时间不增长，是长连接被掐：记为失败项跳过，
  最后统一重试，不要死等

### 方案 A 相关

- Python 端连 CDP **必须 `suppress_origin=True`**，否则 Chromium 以 403 拒绝 WebSocket 握手
- 出版商的文章页是重度 SPA，**必须轮询**等 PDF 链接出现，不能用固定 `sleep`
- 判定「有无机构授权」**必须有正面证据**（`Brought to you by: <机构>` 或确实取到 PDF 链接），
  不能只看"没出现付费墙文案"
- **ScienceDirect 两步法**：导航到 `pdfft` 链接 → 302 到 AWS 签名的
  `pdf.sciencedirectassets.com` 地址 → 在同源页面里 `fetch` 签名地址 → blob → `<a download>`
- **SpringerLink**：`https://link.springer.com/content/pdf/<DOI>.pdf`；
  若 302 跳回 `/article/<DOI>` 说明**无订阅权限**

### 方案 B 相关

- **`bsk download` 对「内联 PDF 链接」失效**（报 `download_capture_failed`）→
  改用 `bsk click --ref '@eN'` + 轮询下载目录，并先设 `always_open_pdf_externally = true`
- **`bsk evaluate` 里 `.blob()` 会让 RPC 超时**（但 JS 其实跑完了）→
  发出去就别等，只轮询下载目录
- **daemon 无法在 Job Object 沙箱里自启** → 必须用持久后台任务跑 `--foreground`；
  默认空转 **10 分钟**就退出，批量前记得 `--daemon-idle 2h`
- **PowerShell 里 `@e23` 会被 splatting 吃掉** → 必须写 `--ref '@e23'`（加引号）
- **装扩展绕过注册表**：`HKCU\Software\Policies\...` 需管理员（普通用户只有 ReadKey），
  外部安装键实测不生效 → 唯一可行是下载 CRX 解包后 `--load-extension`

---

## 7. 仓库结构

```
literature-download-automation/
├── SKILL.md                  # Skill 主文件（决策树 / 前置检查 / 方案细节 / 实战案例）
├── README.md                 # 本文件
├── LICENSE                   # MIT
├── .gitignore
├── examples/
│   └── job.example.json      # 任务文件样例
├── .github/workflows/
│   └── selfcheck.yml         # CI：每次 push 自动跑自检
└── scripts/
    ├── launch_edge.ps1       # 方案 A：启动带 CDP 的 Edge（仅 Windows）
    ├── fetch.py              # 方案 A：批量下载（跨平台）
    ├── fetch_via_bsk.py      # 方案 B：经 bsk CLI 下载（跨平台）
    ├── bsk_setup.ps1         # 方案 B：部署 CLI + 扩展（仅 Windows）
    ├── verify.py             # PDF 校验（跨平台）
    └── selfcheck.py          # 仓库自检（跨平台）
```

---

## 8. 贡献

欢迎提 Issue / PR。特别欢迎补充**新的出版商下载路径**和**已知坑**——
这类知识最值钱的地方就是"别人踩过的坑"。

提交前请确保：

- 改过 frontmatter 后，skill **仍能被正常加载**（见 5.4）
- 新增的 `.ps1` 若含中文，**存为 UTF-8 BOM**
- 不要提交任何**个人信息、机构账号、Cookie 或下载到的论文 PDF**

---

## 9. 许可与免责

本项目以 [MIT License](LICENSE) 发布。

**重要免责声明**：本工具仅用于**个人科研与教学**场景下、对你**已有合法访问权限**的文献
进行批量下载。使用者须自行遵守出版商服务条款、所在机构订阅协议及相关法律法规。
作者不对任何滥用行为负责。
