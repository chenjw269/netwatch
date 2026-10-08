# 打包成单个程序，再用 Inno Setup 做成安装包。
# 用法：powershell -ExecutionPolicy Bypass -File build.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$proxyKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings"
$proxy = Get-ItemProperty $proxyKey -ErrorAction SilentlyContinue
if ($proxy.ProxyEnable -eq 1 -and $proxy.ProxyServer) {
    $server = [string]$proxy.ProxyServer
    if ($server -match "https=([^;]+)") { $server = $Matches[1] }
    elseif ($server -match "http=([^;]+)") { $server = $Matches[1] }
    if ($server -notmatch "://") { $server = "http://$server" }
    $env:HTTP_PROXY = $server
    $env:HTTPS_PROXY = $server
}

$python = (Get-Command python -ErrorAction Stop).Source
$venvPython = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    & $python -m venv (Join-Path $root ".venv")
}

& $venvPython -c "import tkinter"
& $venvPython -m pip install --disable-pip-version-check pyinstaller
& $venvPython -m PyInstaller --noconfirm --clean --windowed --onefile --name NetWatch (Join-Path $root "netwatch.pyw")

$isccCandidates = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
)
$iscc = $isccCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) {
    throw "没有找到 Inno Setup 的 ISCC.exe。安装 Inno Setup 6 后再运行这个脚本。"
}

& $iscc (Join-Path $root "installer\NetWatch.iss")
Write-Host "安装包：$(Join-Path $root 'dist\NetWatch-Setup.exe')"
