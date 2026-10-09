# 发布 WinUI 程序，再用 Inno Setup 做成安装包。
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

$dotnet = Join-Path $env:LOCALAPPDATA "dotnet\dotnet.exe"
if (-not (Test-Path $dotnet)) {
    $dotnet = (Get-Command dotnet -ErrorAction Stop).Source
}
$env:PATH = "$(Split-Path $dotnet);$env:PATH"
$env:DOTNET_ROOT = Split-Path $dotnet
$env:DOTNET_CLI_TELEMETRY_OPTOUT = "1"

& $dotnet publish (Join-Path $root "src\NetWatch.App\NetWatch.App.csproj") `
    -c Release -r win-x64 --self-contained true -p:Platform=x64 `
    -p:DebugType=none -p:DebugSymbols=false --nologo
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

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
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "安装包：$(Join-Path $root 'dist\NetWatch-Setup.exe')"
