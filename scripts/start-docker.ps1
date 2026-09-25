[CmdletBinding()]
param(
    [switch]$Logs
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    Write-Host "已创建 .env，请先填写模型 API Key/Cookie；空配置也可以先启动界面。"
}

docker compose up -d --build
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "second-eye 已启动：http://127.0.0.1:18000"
Write-Host "noVNC 登录页：http://127.0.0.1:16080/vnc.html"
if ($Logs) {
    docker compose logs -f second-eye
}
