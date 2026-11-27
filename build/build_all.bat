@echo off
setlocal EnableExtensions
set "BUILD=%~dp0"
for %%I in ("%BUILD%..") do set "ROOT=%%~fI"
set "SOURCE_ROOT=%ROOT%"
set "OUTPUT=%~1"
set "LOGS=%~2"
set "PYTHON=%~3"
if "%OUTPUT%"=="" set "OUTPUT=%ROOT%\dist"
for %%I in ("%OUTPUT%") do set "OUTPUT=%%~fI"
if "%LOGS%"=="" set "LOGS=%OUTPUT%\reports\logs"
for %%I in ("%LOGS%") do set "LOGS=%%~fI"
if "%PYTHON%"=="" set "PYTHON=python"
if /I "%OUTPUT%"=="%SOURCE_ROOT%" (
    echo Refusing to clean the source directory. Choose a separate output directory. 1>&2
    exit /b 2
)
set "BIN=%OUTPUT%\bin"
set "REPORTS=%OUTPUT%\reports"
set "INTERMEDIATE=%REPORTS%\intermediate"
set "REPRO=%INTERMEDIATE%\reproducibility"
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $taskOutput=[IO.Path]::GetFullPath($env:OUTPUT); $taskLogs=[IO.Path]::GetFullPath($env:LOGS); $taskSource=[IO.Path]::GetFullPath($env:SOURCE_ROOT); if ($taskOutput.TrimEnd('\') -eq [IO.Path]::GetPathRoot($taskOutput).TrimEnd('\')) { throw 'Refusing to clean a drive root.' }; if ($taskOutput.StartsWith(($taskSource.TrimEnd('\')+'\src'),[StringComparison]::OrdinalIgnoreCase)) { throw 'Build output cannot be inside src.' }; for ($taskNode=[IO.DirectoryInfo]::new($taskOutput); $null -ne $taskNode; $taskNode=$taskNode.Parent) { if ($taskNode.Exists -and ($taskNode.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Build output includes a reparse point.' } }; $defaultLogs=[IO.Path]::GetFullPath((Join-Path $env:REPORTS 'logs')); if (Test-Path -LiteralPath $taskOutput) { foreach ($dirName in @('bin','cn','en','backup-tool','reports','.intermediate','.logs','docs','legacy-root')) { $target=Join-Path $taskOutput $dirName; if (-not (Test-Path -LiteralPath $target)) { continue }; if ([string]::Equals($target.TrimEnd('\'),$taskLogs.TrimEnd('\'),[StringComparison]::OrdinalIgnoreCase)) { continue }; if ($dirName -eq 'reports' -and $taskLogs.StartsWith($target.TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase) -and -not [string]::Equals($taskLogs.TrimEnd('\'),$defaultLogs.TrimEnd('\'),[StringComparison]::OrdinalIgnoreCase)) { continue }; if ((Get-Item -LiteralPath $target -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw \"Build output directory is a reparse point: $target\" }; Remove-Item -LiteralPath $target -Recurse -Force }; $staleNames=@('OverFieldLauncher.exe','OverFieldLauncher-EN.exe','LocalUidSDK.dll','build-report.json','SHA256SUMS.txt','capture_checklist.py','capture_import.py','capture_zip.py','client_identity.py','Lan-Firewall.ps1','launcher.ico','launcher.manifest','launcher_updater.py','lolo_redirect.py','make_junction.ps1','model_schema.json','official_capture.py','official_import.py','official_launcher.py','preflight.py','privacy_check.py','profile_manager.py','proto_fields.json','README.md','RELEASE-CHECKLIST.md','RELEASE-NOTES.md','repair_version.py','requirements.txt','room_gateway.py','schema_restore.py','service_runner.py','taptap_private_agent.js','taptap_private_runner.py','update_apply.py','update_policy.py','update_trust.py','World-Control.ps1'); foreach ($taskName in $staleNames) { $taskFile=Join-Path $taskOutput $taskName; if (Test-Path -LiteralPath $taskFile) { Remove-Item -LiteralPath $taskFile -Force } } }"
if errorlevel 1 (
    echo Could not safely clean build outputs. No compilation was attempted. 1>&2
    exit /b 1
)
if not exist "%LOGS%" mkdir "%LOGS%" || exit /b 1
if not exist "%BIN%" mkdir "%BIN%" || exit /b 1
if not exist "%REPORTS%" mkdir "%REPORTS%" || exit /b 1

call powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%BUILD%build_launcher.ps1" -Edition Both -OutputDirectory "%BIN%" -IntermediateDirectory "%INTERMEDIATE%\launcher" > "%LOGS%\launcher-build.log" 2>&1
if errorlevel 1 (
    echo Launcher compilation failed. See "%LOGS%\launcher-build.log". Previous output binaries were removed. 1>&2
    exit /b 1
)
call "%BUILD%build_sdk.cmd" "%BIN%" "%INTERMEDIATE%\sdk" > "%LOGS%\sdk-build.log" 2>&1
if errorlevel 1 (
    echo SDK compilation failed. See "%LOGS%\sdk-build.log". No old DLL was reused. 1>&2
    exit /b 1
)

rem Rebuild from the exact captured inputs, not from possibly edited live sources.
call powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%BUILD%build_launcher.ps1" -Edition Both -OutputDirectory "%REPRO%" -IntermediateDirectory "%REPRO%\.intermediate\launcher" -SourceDirectory "%INTERMEDIATE%\launcher\launcher-src" > "%LOGS%\launcher-repro-build.log" 2>&1
if errorlevel 1 (
    echo Reproducibility rebuild failed. See "%LOGS%\launcher-repro-build.log". 1>&2
    exit /b 1
)
call "%BUILD%build_sdk.cmd" "%REPRO%" "%REPRO%\.intermediate\sdk" "%INTERMEDIATE%\sdk\sdk-src" > "%LOGS%\sdk-repro-build.log" 2>&1
if errorlevel 1 (
    echo SDK reproducibility rebuild failed. See "%LOGS%\sdk-repro-build.log". 1>&2
    exit /b 1
)

set "PYTHONIOENCODING=utf-8"
set "PYTHONPATH=%ROOT%;%ROOT%\src\runtime;%ROOT%\build;%PYTHONPATH%"
pushd "%ROOT%"
"%PYTHON%" "%BUILD%stage_runtime.py" --source "%ROOT%" --output "%OUTPUT%" --binaries "%BIN%" > "%LOGS%\runtime-stage.log" 2>&1
if errorlevel 1 (
    echo Runtime staging failed. See "%LOGS%\runtime-stage.log". 1>&2
    popd
    exit /b 1
)
"%PYTHON%" "%ROOT%\src\runtime\privacy_check.py" > "%LOGS%\privacy-source.log" 2>&1
if errorlevel 1 (
    echo Source privacy check failed. See "%LOGS%\privacy-source.log". 1>&2
    popd
    exit /b 1
)
"%PYTHON%" -m unittest tests.test_build.BuildScriptTests tests.test_editions > "%LOGS%\build-unit-tests.log" 2>&1
if errorlevel 1 (
    echo Build contract tests failed. See "%LOGS%\build-unit-tests.log". 1>&2
    popd
    exit /b 1
)
"%PYTHON%" "%ROOT%\tests\test_build.py" --verify-build "%BIN%" --intermediate "%INTERMEDIATE%" --rebuild-directory "%REPRO%" --staged-directory "%OUTPUT%" --report "%REPORTS%\build-report.json" --logs "%LOGS%" > "%LOGS%\release-verification.log" 2>&1
if errorlevel 1 (
    echo Release verification failed. See "%LOGS%\release-verification.log" and "%REPORTS%\build-report.json". 1>&2
    popd
    exit /b 1
)
"%PYTHON%" "%ROOT%\tests\test_build.py" --write-hashes "%BIN%"
if errorlevel 1 (
    echo Could not write SHA256SUMS.txt. 1>&2
    popd
    exit /b 1
)
echo PASS: CN/EN launchers and x64 SDK built and verified in "%OUTPUT%".
echo Layout: "%OUTPUT%\cn", "%OUTPUT%\en", "%OUTPUT%\backup-tool", "%BIN%", "%REPORTS%"
echo Hashes: "%BIN%\SHA256SUMS.txt"
echo Report: "%REPORTS%\build-report.json"
popd
exit /b 0
