@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
if "%~1"=="" (
    python -B -X utf8 "%~dp0import_to_ofps.py" --gui
) else (
    python -B -X utf8 "%~dp0import_to_ofps.py" --gui "%~1"
)
set "import_exit=%ERRORLEVEL%"
if not "%import_exit%"=="0" pause
exit /b %import_exit%
