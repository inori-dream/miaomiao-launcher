@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0capture.ps1" -Mode Gardens
set "backup_exit=%ERRORLEVEL%"
pause
exit /b %backup_exit%
