@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-conda.ps1" %*
if errorlevel 1 pause
endlocal
