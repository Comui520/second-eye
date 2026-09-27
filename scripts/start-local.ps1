[CmdletBinding()]
param(
    [switch]$Conda,
    [switch]$InstallBrowser,
    [switch]$NoUpdate
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

if ($Conda) {
    & "$PSScriptRoot/start-conda.ps1" -NoUpdate:$NoUpdate
} else {
    & "$PSScriptRoot/start-uv.ps1"
}
exit $LASTEXITCODE
