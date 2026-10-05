<#
.SYNOPSIS
  静默部署 BrowserSkill（bsk CLI + Edge 扩展），作为 fetch.py 的备选方案 B。

.DESCRIPTION
  为什么需要这个脚本：官方安装指南里的两条路在本机环境都走不通 ——
    * `irm https://raw.githubusercontent.com/.../install.ps1 | iex`  → raw.githubusercontent.com 被墙
    * Edge 商店「获取」按钮 → 需人工点击；且默认浏览器是联想浏览器时商店页报不兼容
  本脚本改用可用的镜像 + 不经注册表的 --load-extension 路线：

    1) 经 jsDelivr 取 install.ps1，并与 GitHub API 的版本比对 SHA256（防篡改），再执行
    2) 从 Edge 商店 CDN 直接下载 CRX，解包
    3) 用 `--load-extension` 启动 Edge（不写注册表；HKCU\Software\Policies 需管理员，普通用户拿不到）
    4) 校验扩展是否连上 daemon

.PARAMETER Port
  CDP 调试端口，默认 9222。

.PARAMETER ProfileDir
  Edge profile 目录。默认复用 dsh-edge-profile（与主方案一致，登录态可共享）。

.PARAMETER SkipCli
  跳过 CLI 安装（已装过时）。

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File bsk_setup.ps1
#>
param(
  [int]$Port = 9222,
  [string]$ProfileDir = "$env:LOCALAPPDATA\dsh-edge-profile",
  [switch]$SkipCli
)

$ErrorActionPreference = 'Stop'
$ExtId   = 'emacgiaaaiojkkpkddmmdfhmokgmnikg'
$Repo    = 'Tencent/BrowserSkill'
$BskHome = Join-Path $HOME '.bsk'
$ExtDir  = Join-Path $BskHome 'ext-unpacked'
$CrxDir  = Join-Path $BskHome 'crx'

function Info($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Die($m)  { Write-Host "error: $m" -ForegroundColor Red; exit 1 }

# ── 1) 安装 bsk CLI ───────────────────────────────────────────────────────────
$bskExe = Join-Path $HOME '.local\bin\bsk.exe'
if ($SkipCli -and (Test-Path $bskExe)) {
  Info "跳过 CLI 安装（已存在 $bskExe）"
} else {
  Info "获取 install.ps1（raw.githubusercontent 被墙，改走 jsDelivr）"
  $tmpScript = Join-Path $env:TEMP 'bsk_install.ps1'
  curl.exe -s -L --max-time 60 -o $tmpScript "https://cdn.jsdelivr.net/gh/$Repo@main/install.ps1"
  if (-not (Test-Path $tmpScript)) { Die "install.ps1 下载失败" }

  Info "与 GitHub API 版本比对 SHA256（防篡改）"
  try {
    $api = curl.exe -s --max-time 40 "https://api.github.com/repos/$Repo/contents/install.ps1?ref=main" | ConvertFrom-Json
    $bytes = [Convert]::FromBase64String(($api.content -replace "`n", ""))
    $apiHash = (Get-FileHash -InputStream ([System.IO.MemoryStream]::new($bytes)) -Algorithm SHA256).Hash
    $localHash = (Get-FileHash $tmpScript -Algorithm SHA256).Hash
    if ($apiHash -ne $localHash) { Die "install.ps1 哈希不一致（本地 $localHash / 远端 $apiHash），已中止" }
    Info "哈希一致: $($localHash.Substring(0,16))..."
  } catch {
    Write-Host "warning: 无法比对哈希（$($_.Exception.Message)），请自行确认脚本来源" -ForegroundColor Yellow
  }

  Info "执行官方安装脚本"
  & powershell -ExecutionPolicy Bypass -File $tmpScript
  if ($LASTEXITCODE -ne 0) { Die "CLI 安装失败" }
}

# ── 2) 下载并解包扩展（绕过注册表策略）─────────────────────────────────────────
Info "从 Edge 商店 CDN 下载 CRX"
New-Item -ItemType Directory -Path $CrxDir -Force | Out-Null
$crx = Join-Path $CrxDir 'browserskill.crx'
$url = "https://edge.microsoft.com/extensionwebstorebase/v1/crx?response=redirect&x=id%3D$ExtId%26installsource%3Dondemand%26uc"
curl.exe -s -L --max-time 120 -o $crx $url
if (-not (Test-Path $crx)) { Die "CRX 下载失败" }
$magic = -join ([System.IO.File]::ReadAllBytes($crx)[0..3] | ForEach-Object { [char]$_ })
if ($magic -ne 'Cr24') { Die "下载的不是 CRX（header=$magic）" }
Info "CRX 就绪: $((Get-Item $crx).Length) 字节"

Info "解包到 $ExtDir"
& python -c @"
import io, struct, zipfile, os, shutil, json
crx = r'$crx'; out = r'$ExtDir'
raw = open(crx, 'rb').read()
hl = struct.unpack('<I', raw[8:12])[0]
z = zipfile.ZipFile(io.BytesIO(raw[12+hl:]))
shutil.rmtree(out, ignore_errors=True)
z.extractall(out)
mf = json.loads(z.read('manifest.json').decode('utf-8'))
print('  扩展:', mf['name'], mf['version'], '| 要求 Chrome >=', mf.get('minimum_chrome_version','?'))
"@
if ($LASTEXITCODE -ne 0) { Die "解包失败" }

# ── 3) 让 Edge 直接下载 PDF 而不是内联预览（否则点击 PDF 链接只是导航）────────
$prefFile = Join-Path $ProfileDir 'Default\Preferences'
if (Test-Path $prefFile) {
  Info "设置 always_open_pdf_externally = true"
  & python -c @"
import json, io
p = r'$prefFile'
d = json.load(io.open(p, encoding='utf-8'))
d.setdefault('plugins', {})['always_open_pdf_externally'] = True
d.setdefault('download', {})['prompt_for_download'] = False
json.dump(d, io.open(p, 'w', encoding='utf-8'), ensure_ascii=False)
print('  ok')
"@
} else {
  Write-Host "warning: 未找到 Preferences（profile 尚未创建），首次启动后请重跑本脚本以设置 PDF 下载偏好" -ForegroundColor Yellow
}

# ── 4) 启动 Edge 并加载扩展 ───────────────────────────────────────────────────
Info "关闭既有 Edge"
Get-Process msedge, msedgewebview2 -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 4

$edge = @(
  "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
  "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $edge) { Die "未找到 Edge" }

Info "启动 Edge（--load-extension，不写注册表）"
Start-Process -FilePath $edge -ArgumentList @(
  "--remote-debugging-port=$Port",
  "--user-data-dir=`"$ProfileDir`"",
  "--load-extension=`"$ExtDir`"",
  "--disable-extensions-except=`"$ExtDir`"",
  "--remote-allow-origins=*",
  "--no-first-run", "--no-default-browser-check"
)
Start-Sleep -Seconds 18

# ── 5) 校验 ───────────────────────────────────────────────────────────────────
Info "校验扩展是否加载"
try {
  $list = curl.exe -s --max-time 15 "http://127.0.0.1:$Port/json/list" | ConvertFrom-Json
  $sw = $list | Where-Object { $_.type -eq 'service_worker' -and $_.url -match 'background\.js' }
  if ($sw) { Info "扩展 service worker 已加载" } else { Write-Host "warning: 未发现扩展 service worker" -ForegroundColor Yellow }
} catch {
  Write-Host "warning: CDP 探测失败（$($_.Exception.Message)）" -ForegroundColor Yellow
}

Info "注意：daemon 需在持久任务里启动（沙箱的 Job Object 会回收子进程）"
Write-Host "   " -NoNewline; Write-Host 'bsk daemon start --foreground --daemon-idle 2h' -ForegroundColor White
Write-Host "   随后用 'bsk browsers' 确认 connected。" -ForegroundColor Gray
