[CmdletBinding()]
param(
    [switch]$InstallBrowser,
    [switch]$NoUpdate
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$envName = "good-price"

function Fail([string]$Message) {
    Write-Host "[错误] $Message" -ForegroundColor Red
    Write-Host "按 Enter 退出..." -ForegroundColor DarkGray
    if (-not $env:CI) { Read-Host | Out-Null }
    exit 1
}

function Find-Conda {
    $command = Get-Command conda -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    $candidates = @(
        "$env:USERPROFILE\miniconda3\condabin\conda.bat",
        "$env:USERPROFILE\anaconda3\condabin\conda.bat",
        "$env:LOCALAPPDATA\miniconda3\condabin\conda.bat",
        "C:\ProgramData\miniconda3\condabin\conda.bat",
        "C:\ProgramData\anaconda3\condabin\conda.bat"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) { return $candidate }
    }
    return $null
}

$conda = Find-Conda
if (-not $conda) {
    Fail "未找到 Conda。请先安装 Miniconda/Anaconda，或直接双击 start-uv.cmd（uv 启动不需要 Conda）。"
}

Write-Host "使用 Conda：$conda" -ForegroundColor Cyan
$envList = & $conda env list 2>&1
if ($LASTEXITCODE -ne 0) { Fail "Conda 无法运行，请检查 Conda 安装。`n$($envList -join "`n")" }
$envExists = $envList | Select-String -Pattern "(^|\s)$([regex]::Escape($envName))(\s|$)"

if (-not $envExists) {
    Write-Host "正在创建 Conda 环境 $envName ..." -ForegroundColor Yellow
    & $conda env create -f environment.yml
    if ($LASTEXITCODE -ne 0) { Fail "Conda 环境创建失败。请检查网络或 environment.yml。" }
} elseif (-not $NoUpdate) {
    Write-Host "Conda 环境已存在，检查项目依赖..." -ForegroundColor DarkGray
    & $conda run --no-capture-output -n $envName python -m pip install -e ".[dev]" --disable-pip-version-check
    if ($LASTEXITCODE -ne 0) { Fail "Python 依赖更新失败。" }
}

Write-Host "检查 Playwright Chromium ..." -ForegroundColor DarkGray
& $conda run --no-capture-output -n $envName python -m playwright install chromium
if ($LASTEXITCODE -ne 0) { Fail "Chromium 安装失败。请检查网络，或使用 start-uv.cmd / start-docker.cmd。" }

Write-Host "启动 second-eye：http://127.0.0.1:8000" -ForegroundColor Green
& $conda run --no-capture-output -n $envName python -m goodprice
exit $LASTEXITCODE
