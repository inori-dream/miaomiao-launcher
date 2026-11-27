@echo off
setlocal
set "ROOT=%~dp0"
set "OUTPUT=%~1"
set "INTERMEDIATE=%~2"
set "SOURCE=%~3"
if "%OUTPUT%"=="" set "OUTPUT=%ROOT%"
set "ARGS=-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%ROOT%build_sdk.ps1" -OutputDirectory "%OUTPUT%""
if not "%INTERMEDIATE%"=="" set "ARGS=%ARGS% -IntermediateDirectory "%INTERMEDIATE%""
if not "%SOURCE%"=="" set "ARGS=%ARGS% -SourceDirectory "%SOURCE%""
powershell.exe %ARGS%
exit /b %ERRORLEVEL%
