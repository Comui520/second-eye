@echo off
setlocal
cd /d "%~dp0"
rem Windows Docker quick mode: start with temporary noVNC so first-time login is possible.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-docker.ps1" -Login %*
if errorlevel 1 pause
endlocal
