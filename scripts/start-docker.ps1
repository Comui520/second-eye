[CmdletBinding()]
param(
    [switch]$Login,
    [switch]$Logs,
    [switch]$NoBuild,
    [switch]$NoOpen
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

function Fail([string]$Message) {
    Write-Host "[错误] $Message" -ForegroundColor Red
    Write-Host "按 Enter 退出..." -ForegroundColor DarkGray
    if (-not $env:CI) { Read-Host | Out-Null }
    exit 1
}

function Get-DotEnvValue([string]$Name, [string]$Default) {
    if (-not (Test-Path .env)) { return $Default }
    $line = Get-Content .env | Where-Object { $_ -match "^\s*$([regex]::Escape($Name))\s*=" } | Select-Object -Last 1
    if (-not $line) { return $Default }
    $value = ($line -split "=", 2)[1].Trim().Trim('"').Trim("'")
    if ([string]::IsNullOrWhiteSpace($value)) { return $Default }
    return $value
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Fail "未找到 Docker。请安装并启动 Docker Desktop；Docker 脚本无法替你安装 Docker 引擎。"
}
& docker compose version *> $null
if ($LASTEXITCODE -ne 0) {
    Fail "未找到 Docker Compose。请升级 Docker Desktop，确保 `docker compose version` 可以运行。"
}
& docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Fail "Docker 引擎没有运行。请先启动 Docker Desktop，等待状态变为 Running 后再次运行。"
}

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
        # VNC 密码协议只可靠支持前 8 个字符。
        $password = $password.Substring(0, 8)
        Set-Content -LiteralPath $passwordFile -Value $password -NoNewline
        Write-Host "已生成 noVNC 密码：$password"
    } else {
        $password = (Get-Content $passwordFile -TotalCount 1).Trim()
        Write-Host "使用已有 noVNC 密码：$password"
    }
    $env:ENABLE_NOVNC = "1"
} else {
    $env:ENABLE_NOVNC = "0"
}

& docker compose config --quiet
if ($LASTEXITCODE -ne 0) { Fail ".env 或 compose.yml 配置无效，请检查端口和环境变量。" }

$composeArgs = @("compose", "up", "-d")
if (-not $NoBuild) { $composeArgs += "--build" }
& docker @composeArgs
if ($LASTEXITCODE -ne 0) { Fail "Docker 启动失败，请查看：docker compose logs --tail=100 second-eye" }

$bindAddress = Get-DotEnvValue "BIND_ADDRESS" "127.0.0.1"
$browserHost = $bindAddress
if ($browserHost -eq "0.0.0.0" -or $browserHost -eq "::") { $browserHost = "127.0.0.1" }
Write-Host "second-eye 已启动：http://${browserHost}:18000" -ForegroundColor Green
if ($Login) {
    Write-Host "登录模式已启用 noVNC：http://${browserHost}:16080/vnc.html" -ForegroundColor Green
    Write-Host "登录完成后请重新执行：.\scripts\start-docker.ps1（关闭 noVNC）"
    if (-not $NoOpen) {
        Start-Process "http://${browserHost}:18000/settings"
        Start-Process "http://${browserHost}:16080/vnc.html"
    }
} else {
    Write-Host "普通运行模式：noVNC 已关闭。需要登录时执行 .\scripts\start-docker.ps1 -Login"
}

if ($Logs) {
    docker compose logs -f second-eye
}
