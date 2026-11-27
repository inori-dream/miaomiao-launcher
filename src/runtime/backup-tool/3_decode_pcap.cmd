@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
if "%~1"=="" (
    echo Drag a .pcapng file onto this script, or pass it as the first argument.
    pause
    exit /b 2
)
"%~dp0tools\python\python.exe" -B -X utf8 "%~dp0parse_pcap.py" "%~1"
set "backup_exit=%ERRORLEVEL%"
pause
exit /b %backup_exit%
