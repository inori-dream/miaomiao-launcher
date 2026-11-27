@echo off
setlocal EnableExtensions
set "ROOT=%~dp0"
for %%I in ("%ROOT%") do set "SOURCE_ROOT=%%~fI"
set "OUTPUT=%~1"
set "LOGS=%~2"
set "PYTHON=%~3"
if "%OUTPUT%"=="" set "OUTPUT=C:\OverFieldLab\runtime\launcher-build"
if "%LOGS%"=="" set "LOGS=C:\OverFieldLab\logs"
if "%PYTHON%"=="" set "PYTHON=python"
for %%I in ("%OUTPUT%") do set "OUTPUT=%%~fI"
if /I "%OUTPUT%"=="%SOURCE_ROOT%" (
    echo Refusing to clean the source directory. Choose a separate output directory. 1>&2
    exit /b 2
)
if not exist "%LOGS%" mkdir "%LOGS%" || exit /b 1
if not exist "%OUTPUT%" mkdir "%OUTPUT%" || exit /b 1
set "INTERMEDIATE=%OUTPUT%\.intermediate"
set "REPRO=%INTERMEDIATE%\reproducibility"
if exist "%INTERMEDIATE%" rmdir /s /q "%INTERMEDIATE%"
for %%F in (OverFieldLauncher.exe OverFieldLauncher-EN.exe LocalUidSDK.dll build-report.json SHA256SUMS.txt) do if exist "%OUTPUT%\%%F" del /f /q "%OUTPUT%\%%F"

call powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%ROOT%build_launcher.ps1" -Edition Both -OutputDirectory "%OUTPUT%" -IntermediateDirectory "%INTERMEDIATE%\launcher" > "%LOGS%\launcher-build.log" 2>&1
if errorlevel 1 (
    echo Launcher compilation failed. See "%LOGS%\launcher-build.log". Previous output binaries were removed. 1>&2
    exit /b 1
)
call "%ROOT%build_sdk.cmd" "%OUTPUT%" "%INTERMEDIATE%\sdk" > "%LOGS%\sdk-build.log" 2>&1
if errorlevel 1 (
    echo SDK compilation failed. See "%LOGS%\sdk-build.log". No old DLL was reused. 1>&2
    exit /b 1
)

rem Rebuild from the exact captured inputs, not from possibly edited live sources.
call powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%ROOT%build_launcher.ps1" -Edition Both -OutputDirectory "%REPRO%" -IntermediateDirectory "%REPRO%\.intermediate\launcher" -SourceDirectory "%INTERMEDIATE%\launcher\launcher-src" > "%LOGS%\launcher-repro-build.log" 2>&1
if errorlevel 1 (
    echo Reproducibility rebuild failed. See "%LOGS%\launcher-repro-build.log". 1>&2
    exit /b 1
)
call "%ROOT%build_sdk.cmd" "%REPRO%" "%REPRO%\.intermediate\sdk" "%INTERMEDIATE%\sdk\sdk-src" > "%LOGS%\sdk-repro-build.log" 2>&1
if errorlevel 1 (
    echo SDK reproducibility rebuild failed. See "%LOGS%\sdk-repro-build.log". 1>&2
    exit /b 1
)

set "PYTHONIOENCODING=utf-8"
pushd "%ROOT%"
"%PYTHON%" "%ROOT%privacy_check.py" > "%LOGS%\privacy-source.log" 2>&1
if errorlevel 1 (
    echo Source privacy check failed. See "%LOGS%\privacy-source.log". 1>&2
    exit /b 1
)
"%PYTHON%" -m unittest tests.test_build.BuildScriptTests tests.test_editions > "%LOGS%\build-unit-tests.log" 2>&1
if errorlevel 1 (
    echo Build contract tests failed. See "%LOGS%\build-unit-tests.log". 1>&2
    exit /b 1
)
"%PYTHON%" "%ROOT%tests\test_build.py" --verify-build "%OUTPUT%" --rebuild-directory "%REPRO%" --report "%OUTPUT%\build-report.json" --logs "%LOGS%" > "%LOGS%\release-verification.log" 2>&1
if errorlevel 1 (
    echo Release verification failed. See "%LOGS%\release-verification.log" and build-report.json. 1>&2
    exit /b 1
)
"%PYTHON%" "%ROOT%tests\test_build.py" --write-hashes "%OUTPUT%"
if errorlevel 1 (
    echo Could not write SHA256SUMS.txt. 1>&2
    exit /b 1
)
echo PASS: CN/EN launchers and x64 SDK built and verified in "%OUTPUT%".
echo Hashes: "%OUTPUT%\SHA256SUMS.txt"
echo Report: "%OUTPUT%\build-report.json"
popd
exit /b 0
