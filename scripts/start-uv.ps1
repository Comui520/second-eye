[CmdletBinding()]
param(
    [switch]$InstallBrowser
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

function Fail([string]$Message) {
    Write-Host "[错误] $Message" -ForegroundColor Red
    Write-Host "按 Enter 退出..." -ForegroundColor DarkGray
    if (-not $env:CI) { Read-Host | Out-Null }
    exit 1
}

function Find-Uv {
    $command = Get-Command uv -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    $candidates = @(
        "$env:USERPROFILE\.local\bin\uv.exe",
        "$env:LOCALAPPDATA\uv\uv.exe"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) { return $candidate }
    }
    return $null
}

$uv = Find-Uv
if (-not $uv) {
    Write-Host "未找到 uv，尝试自动安装 uv（需要网络）..." -ForegroundColor Yellow
    $installer = Join-Path $env:TEMP "second-eye-install-uv.ps1"
    try {
        Invoke-WebRequest -Uri "https://astral.sh/uv/install.ps1" -OutFile $installer -UseBasicParsing
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer
    } catch {
        Fail "uv 自动安装失败：$($_.Exception.Message)`n请手动安装 uv 后再次运行，或使用 start-conda.cmd / start-docker.cmd。"
    }
    $uv = Find-Uv
}
if (-not $uv) { Fail "uv 安装后仍未找到 uv.exe。请重开终端后再次运行。" }

Write-Host "使用 uv：$uv" -ForegroundColor Cyan
Write-Host "正在同步 Python 3.11 环境和项目依赖..." -ForegroundColor Yellow
& $uv sync --python 3.11 --extra dev
if ($LASTEXITCODE -ne 0) { Fail "uv 环境同步失败。请检查网络或 pyproject.toml。" }

Write-Host "检查 Playwright Chromium ..." -ForegroundColor DarkGray
& $uv run python -m playwright install chromium
if ($LASTEXITCODE -ne 0) { Fail "Chromium 安装失败。请检查网络，或使用 start-docker.cmd。" }

Write-Host "启动 second-eye：http://127.0.0.1:8000" -ForegroundColor Green
& $uv run python -m goodprice
exit $LASTEXITCODE
