[CmdletBinding()]
param(
    [switch]$Conda,
    [switch]$InstallBrowser
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

if ($Conda) {
    if (-not (Get-Command conda -ErrorAction SilentlyContinue)) {
        throw "未找到 conda。请安装 Miniconda/Anaconda，或不带 -Conda 使用 uv。"
    }
    $envExists = conda env list | Select-String -Pattern "^good-price\s"
    if (-not $envExists) {
        conda env create -f environment.yml
    }
    if ($InstallBrowser) {
        conda run -n good-price python -m playwright install chromium
    }
    conda run --no-capture-output -n good-price python -m goodprice
    exit $LASTEXITCODE
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "未找到 uv。请安装 uv，或使用 .\scripts\start-local.ps1 -Conda。"
}

uv sync --python 3.11 --extra dev
if ($InstallBrowser -or -not (Test-Path "$env:USERPROFILE\\AppData\\Local\\ms-playwright")) {
    uv run python -m playwright install chromium
}
uv run python -m goodprice
exit $LASTEXITCODE
