[CmdletBinding()]
param(
    [switch]$Login,
    [switch]$Logs,
    [switch]$NoBuild
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    Write-Host "已创建 .env，请先填写模型 API Key/Cookie；空配置也可以先启动界面。"
}

New-Item -ItemType Directory -Force data | Out-Null

# Docker 中的浏览器不能直接显示到 Windows 桌面。-Login 会临时启用带密码的 noVNC，
# 让只安装 Docker 的用户也能完成一次登录；普通启动则始终关闭 noVNC。
if ($Login) {
    $passwordFile = Join-Path (Get-Location) "data/.novnc-password"
    if (-not (Test-Path $passwordFile) -or [string]::IsNullOrWhiteSpace((Get-Content $passwordFile -TotalCount 1))) {
        $bytes = New-Object byte[] 24
        $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
        $rng.GetBytes($bytes)
        $rng.Dispose()
        $password = [Convert]::ToBase64String($bytes).Replace('+', 'A').Replace('/', 'B').TrimEnd('=')
        $password = $password.Substring(0, 8)
        Set-Content -LiteralPath $passwordFile -Value $password -NoNewline
        Write-Host "已生成 noVNC 临时密码：$password"
    } else {
        $password = (Get-Content $passwordFile -TotalCount 1).Trim()
        Write-Host "使用已有 noVNC 密码：$password"
    }
    $env:ENABLE_NOVNC = "1"
} else {
    $env:ENABLE_NOVNC = "0"
}

$composeArgs = @("compose", "up", "-d")
if (-not $NoBuild) { $composeArgs += "--build" }
& docker @composeArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "second-eye 已启动：http://127.0.0.1:18000"
if ($Login) {
    Write-Host "登录模式已启用 noVNC：http://127.0.0.1:16080/vnc.html"
    Write-Host "登录完成后请重新执行：.\scripts\start-docker.ps1（关闭 noVNC）"
    Start-Process "http://127.0.0.1:18000/settings"
    Start-Process "http://127.0.0.1:16080/vnc.html"
} else {
    Write-Host "普通运行模式：noVNC 已关闭。需要登录时执行 .\scripts\start-docker.ps1 -Login"
}

if ($Logs) {
    docker compose logs -f second-eye
}
