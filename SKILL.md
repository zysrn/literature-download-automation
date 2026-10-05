---
name: literature-download-automation
description: Batch-download full-text PDFs for a list of DOIs/titles through a real browser, using the user's own institutional access (CARSI / 校园统一身份认证 / VPN) plus open-access routes. Two interchangeable drivers — a self-contained CDP script (default) and Tencent BrowserSkill as a fallback that reuses the user's real logged-in browser. Use when the user asks to download papers to disk, or when a plain DOI/curl fetch returns HTML or 403 instead of a PDF.
whenToUse: 用户要求把一批文献/论文/PDF 下载到本地；或直接抓取 DOI 只得到 HTML/403 而非 PDF；或需要批量获取订阅制（付费墙）文献全文并整理清单。
---

# 批量下载文献全文

## 0. 边界与原则（必读）

- **只走两条合法渠道**：① 开放获取（OA）② 用户所在机构的**已购订阅**。不要使用盗版镜像站。
- **绝不索取或经手用户的账号密码**。统一身份认证 / CARSI / 扫码登录必须由**用户本人**完成；
  Agent 只负责登录**之后**的自动化。用户主动要发密码给你时，劝阻他。
- **不尝试绕过付费墙，也不自动破解 CAPTCHA**。人机验证（Cloudflare Turnstile 等）是站点的
  反机器人措施，属于「必须由人完成的一步」——方案 B 内置 `request-help` 交接，
  方案 A 则直接请用户在弹出的浏览器窗口里点一下。
- 拿不到的文献：**如实记录为失败项并在报告中说明技术原因**，不要想办法硬绕。
- 下载前留意机构版权规定；仅用于个人科研/教学用途。

---

## 1. 决策树

```
要下载一批文献
├─ 1) 先查 OA（免费、最快，不需要浏览器）
│    Unpaywall / OpenAlex / DOAJ / arXiv / 期刊自建站
│    → 命中就直接下
└─ 2) 剩余的走机构订阅（需要真实浏览器 + 用户登录一次）
     ├─ 方案 A【默认】裸 CDP + fetch.py      —— 零依赖、下载最稳
     ├─ 方案 B【备选】BrowserSkill           —— 复用真实登录态、内置人工交接
     └─ 方案 C【轻量】Playwright             —— 只想少写代码时
```

**OA 批量探测**（免费、无需登录，先做这一步能省掉大量浏览器操作）：

```
https://api.unpaywall.org/v2/<DOI>?email=<任意邮箱>
https://api.openalex.org/works/doi:<DOI>       # 看 best_oa_location / open_access
https://api.semanticscholar.org/graph/v1/paper/DOI:<DOI>?fields=openAccessPdf
```
另有期刊自建站常有免费全文（例：`jmst.org`、`ams.org.cn`、`ysxbcn.com`、`xb.sut.edu.cn` 等
国内期刊官网，常见形式 `downloadArticleFile.do?attachType=PDF&id=<N>`）。

---

## 2. 前置检查清单（动手前 30 秒）

1. 确认目标目录，先 `mkdir` 好。
2. 依赖：`python -m pip install websocket-client pypdf`
   （方案 C 另需 `playwright`）
3. **确认用户的机构远程访问入口**：
   - CARSI 门户：`https://ds.carsi.edu.cn/`
   - 学校 IdP：`https://idp.<学校>.edu.cn`
   - CAS：`https://cas.<学校>.edu.cn`
   - WebVPN：`https://webvpn.<学校>.edu.cn/`（**很多学校这条是坏的**，返回「应用未注册」就放弃）
   - 校园 VPN 客户端（须用户在**本机**连接，Agent 无法代劳）
4. 探测网络实况：
   ```powershell
   curl.exe -s -o NUL -w "%{http_code}" https://www.sciencedirect.com/   # 403 说明有反爬
   curl.exe -s --max-time 15 https://ifconfig.me/ip                       # 当前出口 IP
   ```
   **Google / Google Scholar 返回 000（不可达）说明处于受限网络**，只能走机构通道。

---

## 3. 方案 A【默认】受控 Edge + 裸 CDP

零依赖、下载路径最可控，ScienceDirect 两步法 6/6 稳定。

```powershell
powershell -ExecutionPolicy Bypass -File scripts/launch_edge.ps1 `
    -Port 9222 -StartUrl "https://ds.carsi.edu.cn/"
```

要点（已封装在脚本里，改时别删）：
- 必须用**独立** `--user-data-dir`（默认 profile 不接受远程调试）
- 必须加 `--remote-allow-origins=*`
- Python 端连 WebSocket **必须 `suppress_origin=True`**，否则新版 Chromium 以
  `403 Rejected an incoming WebSocket connection` 拒绝握手

然后**请用户在该窗口里完成统一身份认证登录**。

> 连通性验证：`python scripts/fetch.py --check`（探测 ScienceDirect / SpringerLink 授权状态）

### 执行下载

`job.json`：

```json
{
  "outdir": "C:\\\\Users\\\\me\\\\Desktop\\\\文献",
  "items": [
    {"file": "01_xxx_MSEA2019.pdf", "doi": "10.1016/j.msea.2018.09.055"},
    {"file": "02_yyy_MMI2018.pdf",  "doi": "10.1007/s12540-018-0030-x"}
  ]
}
```

```powershell
python scripts/fetch.py job.json      # 先跑 1 篇验证，再批量
```

### 关键：ScienceDirect 必须用「两步法」（`fetch.py` 已实现）

```
① 用真实页面导航到 pdfft 链接
   https://www.sciencedirect.com/science/article/pii/<PII>/pdfft?md5=...&pid=...
   → 服务器 302 到带 AWS 签名的 S3 直链：
     https://pdf.sciencedirectassets.com/<...>/main.pdf?X-Amz-Security-Token=...
② 回到文章页，用同源 fetch 拿那个签名直链 → blob → <a download> 触发下载
```

**为什么不能一步**：
- 直接 `fetch` 文章页/html 或 `pdfft` 链接 → 反爬识别，返回 **HTML 挑战页**（50~350 字节）
- 签名直链**脱离浏览器会话**（curl/urllib 直接抓）→ 失效，返回 HTML
- 只有「浏览器导航取链 + 页面内 fetch」稳定成功

### SpringerLink

PDF 直链规律：`https://link.springer.com/content/pdf/<DOI>.pdf`
- **有**权限：该 URL 直接给 PDF
- **无**权限：**302 重定向回 `/article/<DOI>`** —— 这就是无权限的判定信号

---

## 4. 方案 B【备选】BrowserSkill（Tencent/BrowserSkill）

### 什么时候选它

| 场景 | 为什么方案 B 更合适 |
|---|---|
| 用户已在真实浏览器里登录了机构账号 | **直接复用，无需重新登录**（方案 A 的独立 profile 要重登） |
| 需要人工介入（CAPTCHA、扫码、短信验证） | 内置 `request-help` 交接，方案 A 需自己实现 |
| 方案 A 的裸 CDP 被反爬拦住 | 真扩展驱动的浏览器更像真人 |
| 想让用户**看见** Agent 在做什么 | DSH Web UI 有实时浏览器视图 |

### 静默部署（`scripts/bsk_setup.ps1`）

```powershell
powershell -ExecutionPolicy Bypass -File scripts/bsk_setup.ps1
```

脚本做了四件事，**每一步都是被现实逼出来的**：
1. **装 bsk CLI**：官方一行命令 `irm https://raw.githubusercontent.com/.../install.ps1 | iex`
   —— **`raw.githubusercontent.com` 在国内被墙（000）**，所以改走 jsDelivr 镜像，
   并与 GitHub API 的版本**比对 SHA256** 防篡改后再执行。
2. **装 Edge 扩展**：`HKCU\Software\Policies\...\ExtensionInstallForcelist`
   —— **需要管理员**（`HKCU\Software\Policies` 普通用户只有 ReadKey），不可用；
   `HKCU\Software\Microsoft\Edge\Extensions\<id>` 外部安装键 —— 可写但**实测不生效**。
   最终**唯一可行**的是：从 Edge 商店 CDN 下 CRX → 解包 → `--load-extension` 加载。
3. **让 PDF 直接下载**：写 profile 的 `Preferences` 把 `plugins.always_open_pdf_externally`
   设为 `true`，否则点 PDF 链接只是**导航到内联阅读器**，不产生下载。
4. 启动 Edge 并校验扩展的 service worker 是否加载。

> ⚠️ 扩展若是**解包加载**，其 ID 是按路径派生的（≠ 商店 ID），这是正常的。
> 实测扩展会**自动连上 daemon，无需用户点弹窗**。

### 启动 daemon（沙箱环境必须注意）

沙箱里每条命令是独立 Job Object，**daemon 无法自启**。必须用**持久后台任务**：

```powershell
bsk daemon start --foreground --daemon-idle 2h
```

- `--daemon-idle` 默认仅 **10 分钟**，不设长一点会在批量中途退出
- 之后用 `bsk browsers` 确认输出里有 connected 的浏览器

### 执行下载

```powershell
python scripts/fetch_via_bsk.py job.json
```

脚本里的策略顺序（因为 `bsk download` 有 bug，见下）：
1. `bsk snapshot` 找到 PDF 链接的 `@eN` 引用 → `bsk click --ref '@eN'` 触发原生下载
   → 轮询系统下载目录捞文件
2. 失败则降级：`bsk evaluate` 里 `fetch` + `blob` + `<a download>`
   → **不等 RPC 返回**（它一定会超时），直接轮询下载目录

### 已知限制（实测 v0.3.1，报 issue 时可引用）

| # | 问题 | 现象 | 规避 |
|---|---|---|---|
| 1 | **`bsk download` 对「内联 PDF 链接」失效** | `download_capture_failed`；`effect_state` 是 `committed` 但捕获不到 | 改用 `bsk click` + 轮询下载目录；并确保已设 `always_open_pdf_externally` |
| 2 | **`bsk evaluate` 里 `.blob()` 让 RPC 挂死** | 90s+ 超时；但 **JS 其实跑完了**，文件照样下下来 | 用 `--await-promise false` 发出去就别等，只轮询下载目录 |
| 3 | daemon 无法在 Job Object 沙箱里自启 | `doctor` 报 `cannot start an independent Windows daemon` | 用持久后台任务跑 `--foreground`（见上） |
| 4 | **PowerShell 里 `@e23` 会被 splatting 吃掉** | 报 `missing target` | 必须写 `--ref '@e23'`（加引号）；Python 用 subprocess 列表传参则无此问题 |

> 另外：**DSH 插件暴露的 `browser_*` 工具集不含 `evaluate`**（官方明说不支持任意脚本求值），
> 但 **CLI 有 `evaluate`**。所以要走两步法就用 CLI（本 skill 的 `fetch_via_bsk.py` 即是），
> 不要指望插件的工具。插件的 `browser_*` 需**重启 dsh profile** 才注册。

---

## 5. 方案 C【轻量】Playwright

只是想少写点代码时：

```python
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(channel="msedge", headless=True)   # 复用系统 Edge，不下载内核
```

优点：API 简单、自带等待、无需装扩展。
缺点：**无法复用用户已登录的那个窗口**（登录态在独立 profile 里），启动更重。
需要真实登录态时选方案 B，不需要时方案 A 最省事。

---

## 6. 常见故障与排查

| 症状 | 原因 | 处置 |
|---|---|---|
| WebSocket 握手 403 | Origin 校验 | Python 端 `suppress_origin=True`；浏览器加 `--remote-allow-origins=*` |
| 抓到的「PDF」是几十~几百 KB 的 HTML | 反爬挑战页 | ScienceDirect 用两步法（§3） |
| **文章页 `<a>` 只有个位数、找不到 PDF 链接** | **文章页是重度 SPA，固定 sleep 不够**（首次导航尤其慢；同一 DOI 第二次往往 5 秒就有 300+ 链接） | **必须轮询**到出现 pdf 链接（`poll_pdf_meta()`，超时 45s），别用固定 `sleep`。两个方案都实现了这个轮询 |
| 文件没出现在目标目录 | blob 下载常落到**系统默认下载目录** | 同时扫 `~/Downloads` 和 outdir，按 mtime 找最新文件再移动 |
| `edge://downloads-hub/` 变成「当前页」 | 下载触发了下载中心标签 | 选 page target 时**优先 `http(s)`**，并关闭 `edge://` 内部页 |
| **误判「有权限」** | 用「没检测到付费墙文案」当成功判据 → SPA 未渲染时两者皆空，假阳性 | **必须用正面证据**：页面出现 `Brought to you by: <机构>` 或确实取到 PDF 链接 |
| 点 PDF 链接只是**导航到阅读器**、不下载 | 浏览器默认内联预览 PDF | 设 `plugins.always_open_pdf_externally = true`（`bsk_setup.ps1` 已含） |
| 机构认证流程全部 200，但仍显示付费墙 | 该库是 **IP 授权制**（如 DRAA 联盟采购），联邦认证不携带权益 | **判定无解**：记为失败项并说明原因，不要硬绕 |
| 登录后页面「没变化」 | 只完成了门户认证，**没点「访问资源」** | 必须点资源条目的「访问资源」，身份才会带给出版商 |
| 出现 CAPTCHA / 人机验证 | 站点反爬（Cloudflare Turnstile 等） | **这是必须由人完成的一步**：方案 B 用 `request-help` 交接；方案 A 直接请用户在窗口里点一下。**不要尝试自动破解** |
| `.ps1` 报「字符串缺少终止符」/中文乱码 | **PowerShell 5.1 读无 BOM 的 UTF-8 脚本会按 GBK 解码** | 带中文的 `.ps1` **必须以 UTF-8 BOM 保存**（本 skill 的两个 ps1 都已带 BOM） |
| 找不到机构入口 | — | CARSI 各资源的上线流程文档在 `https://mgmt.carsi.edu.cn/member_files/<publisher>/...` |
| **同一 URL 一会儿 000 一会儿 200** | **网络间歇性抖动**，不等于稳定的「被墙」（实测 GitHub 通道时通时断；出口 IP 也会在会话间变化） | **不要一次失败就判定为墙**：间隔几秒重试 2~3 次再下结论。本 skill 依赖的镜像 / CRX / API 端点都建议先探一次可达性 |
| 下载中途卡住不动（`.crdownload` 不再增长） | 同上，长连接被掐 | 记为该篇失败并继续下一篇，最后统一重试；不要死等 |
| `bsk` 每 30 分钟报 `periodic update check failed` | daemon 的自更新检查走 GitHub Releases | **无害**，不影响下载；要更新就重跑 `bsk_setup.ps1` |

### 判定「机构授权是否生效」的可靠信号

- **ScienceDirect**：页面顶部出现 `Brought to you by: <机构名>` ✅
- **SpringerLink**：右上角显示机构名（而非 "Log in"），且文章页出现绿色 **Download PDF**
  而不是 **Buy article PDF**（含价格）❌
- 更硬的判定：直接请求 PDF URL，看是回 PDF 还是被重定向回文章页

### 诊断工具：抓完整跳转链

认证疑难时用 CDP 的 `Network.enable` 记录 `requestWillBeSent` / `responseReceived`，
把整条 SAML 链打出来。典型健康链：

```
WAYF/门户 → SP 端点(sp.springer.com / auth.elsevier.com) → 学校 IdP
          → SP 消费者(fsso.springer.com / ...) → 出版商站点
```
链条完整但无权益 → IP 授权问题（无解）；中间断裂 → 排查第三方 Cookie
（**无痕窗口常常直接解决**）或广告拦截插件。

---

## 7. 交付规范

- 文件名统一：`编号_主题_期刊年份.pdf`（编号与用户的清单对齐）
- **每份都要校验**：`python scripts/verify.py <目录>` —— 检查 `%PDF-` 文件头 + pypdf 页数
- 附带生成 `清单.md`：每篇的状态（已获取 / OA 免费 / 失败）与链接
- 报告时**如实区分**失败类型：无权限（IP 授权制 / 未订阅）、反爬拦截、CAPTCHA 待人工，
  并说明技术原因

---

## 8. 本 skill 附带脚本

| 脚本 | 用途 |
|---|---|
| `scripts/launch_edge.ps1` | 方案 A：启动带 CDP 的受控 Edge（独立 profile） |
| `scripts/fetch.py` | 方案 A：批量下载主程序（`--check` 探测授权；按出版商自动选策略） |
| `scripts/bsk_setup.ps1` | 方案 B：静默部署 BrowserSkill（CLI + 扩展，绕过注册表限制） |
| `scripts/fetch_via_bsk.py` | 方案 B：用 bsk CLI 批量下载 |
| `scripts/verify.py` | PDF 校验：文件头 + 页数 + 首页文本，可输出清单 JSON |

---

## 9. 实战参考案例

**GUET（桂林电子科技大学）· 13 篇镁合金文献 · 2026-09**

- 机构通道：CARSI（`ds.carsi.edu.cn` → 统一身份认证）。订阅含 ScienceDirect + SpringerLink。
- **结果 11/13**：
  - ScienceDirect 6 篇 **全部成功**（两步法）
  - 开放获取 5 篇（含核心的 AM30 预孪晶高速冲击论文）
  - **失败 2 篇（Springer）**：SAML 链完整走通（WAYF → 桂电 IdP → `fsso.springer.com`
    全部 200，`.springer.com` 上确实设了 `idp_session` cookie），但文章页仍
    `Buy article PDF 39,95 €` → 判定 **IP 授权制**，无校园网/VPN 拿不到。
- 该案例确认的现实约束：
  - 学校 **WebVPN 是坏的**（「统一身份认证 应用未注册」），**SSL VPN 502**
    —— 探测入口时不要想当然
  - ScienceDirect 会在异常流量后弹 **Cloudflare Turnstile**，属于必须人工的一步
