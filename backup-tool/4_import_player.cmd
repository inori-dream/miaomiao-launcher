@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
if "%~1"=="" (
    if exist "%~dp0tools\python\python.exe" (
        "%~dp0tools\python\python.exe" -B -X utf8 "%~dp0import_to_ofps.py" --gui
    ) else (
        python -B -X utf8 "%~dp0import_to_ofps.py" --gui
    )
) else (
    if exist "%~dp0tools\python\python.exe" (
        "%~dp0tools\python\python.exe" -B -X utf8 "%~dp0import_to_ofps.py" --gui "%~1"
    ) else (
        python -B -X utf8 "%~dp0import_to_ofps.py" --gui "%~1"
    )
)
set "import_exit=%ERRORLEVEL%"
if not "%import_exit%"=="0" pause
exit /b %import_exit%
