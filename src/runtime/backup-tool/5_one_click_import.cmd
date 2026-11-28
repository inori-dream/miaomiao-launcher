@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
set "TOOL_DIR=%~dp0"
set "ROOT_DIR=%TOOL_DIR%.."
set "SCRIPT=%ROOT_DIR%\app\one_click_import.py"
if not exist "%SCRIPT%" set "SCRIPT=%TOOL_DIR%..\one_click_import.py"
set "PY_EXE=%ROOT_DIR%\runtime-python\python.exe"
if not exist "%PY_EXE%" set "PY_EXE=%TOOL_DIR%tools\python\python.exe"
if not exist "%PY_EXE%" set "PY_EXE=python"
if "%~1"=="" (
    "%PY_EXE%" -B -X utf8 "%SCRIPT%" gui
) else (
    "%PY_EXE%" -B -X utf8 "%SCRIPT%" gui "%~1"
)
set "import_exit=%ERRORLEVEL%"
if not "%import_exit%"=="0" pause
exit /b %import_exit%
