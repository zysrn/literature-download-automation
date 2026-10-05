param(
  [int]$Port = 9222,
  [string]$ProfileDir = "$env:LOCALAPPDATA\dsh-edge-profile",
  [string]$StartUrl = "about:blank"
)
# 启动一个带远程调试的 Edge 实例，供 Agent 通过 CDP 驱动。
$ErrorActionPreference = 'Stop'
$cands = @(
  "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
  "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
  "$env:LOCALAPPDATA\Microsoft\Edge\Application\msedge.exe",
  "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
  "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe"
)
$exe = $cands | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $exe) { Write-Error "未找到 Edge / Chrome"; exit 1 }

New-Item -ItemType Directory -Path $ProfileDir -Force | Out-Null
Start-Process -FilePath $exe -ArgumentList @(
  "--remote-debugging-port=$Port",
  "--user-data-dir=`"$ProfileDir`"",
  "--no-first-run",
  "--no-default-browser-check",
  "--remote-allow-origins=*",
  $StartUrl
) | Out-Null

for ($i = 0; $i -lt 40; $i++) {
  Start-Sleep -Milliseconds 500
  try {
    $v = Invoke-RestMethod "http://127.0.0.1:$Port/json/version" -TimeoutSec 3
    Write-Output "CDP 就绪: $($v.Browser)  ws=$($v.webSocketDebuggerUrl)"
    Write-Output "profile: $ProfileDir"
    exit 0
  } catch { }
}
Write-Error "CDP 在 20 秒内未就绪（port=$Port）"
exit 1
