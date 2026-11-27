param(
    [string]$OutputDirectory='',
    [string]$IntermediateDirectory='',
    [string]$SourceDirectory=''
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$taskProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (-not $SourceDirectory) { $SourceDirectory = $taskProjectRoot }
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $taskProjectRoot 'dist\bin' }
$taskRoot = [IO.Path]::GetFullPath($SourceDirectory)
$taskOutput = [IO.Path]::GetFullPath($OutputDirectory)
$taskKeepIntermediate = $PSBoundParameters.ContainsKey('IntermediateDirectory')
if (-not $IntermediateDirectory) {
    $IntermediateDirectory = Join-Path ([IO.Path]::GetTempPath()) ("miaomiao-launcher\{0}\sdk" -f [Guid]::NewGuid().ToString('N'))
}
$taskIntermediate = [IO.Path]::GetFullPath($IntermediateDirectory)
$taskSnapshot = Join-Path $taskIntermediate 'sdk-src'
$taskInputs = @('LocalUidSDK.cpp', 'sdk.def')
function Get-SdkInput([string]$Name) {
    $flat = Join-Path $taskRoot $Name
    if ([IO.File]::Exists($flat)) { return $flat }
    return Join-Path (Join-Path $taskRoot 'src\sdk') $Name
}
foreach ($name in $taskInputs) {
    $path = Get-SdkInput $name
    if (-not [IO.File]::Exists($path)) { throw "Required build input is missing: $path" }
}
[IO.Directory]::CreateDirectory($taskSnapshot) | Out-Null
[IO.Directory]::CreateDirectory($taskOutput) | Out-Null
foreach ($name in $taskInputs) { [IO.File]::Copy((Get-SdkInput $name), (Join-Path $taskSnapshot $name), $true) }

$taskVsWhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
if (-not [IO.File]::Exists($taskVsWhere)) {
    $command = Get-Command vswhere.exe -ErrorAction SilentlyContinue
    if (-not $command) { throw 'vswhere.exe is required to locate Visual Studio Build Tools.' }
    $taskVsWhere = $command.Source
}
$taskInstall = (& $taskVsWhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath | Select-Object -First 1)
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($taskInstall)) { throw 'Visual Studio C++ x64 Build Tools were not found.' }
$taskInstall = [IO.Path]::GetFullPath($taskInstall.Trim())
$taskToolsRoot = Join-Path $taskInstall 'VC\Tools\MSVC'
$taskVersionFile = Join-Path $taskInstall 'VC\Auxiliary\Build\Microsoft.VCToolsVersion.default.txt'
$taskVersion = if ([IO.File]::Exists($taskVersionFile)) {
    ([IO.File]::ReadAllText($taskVersionFile)).Trim()
} else {
    $versions = Get-ChildItem -LiteralPath $taskToolsRoot -Directory -ErrorAction SilentlyContinue | Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName 'bin\Hostx64\x64\cl.exe') }
    if (-not $versions) { throw 'The selected Visual Studio installation has no x64 C++ compiler.' }
    ($versions | Sort-Object Name -Descending | Select-Object -First 1 -ExpandProperty Name)
}
$taskToolset = Join-Path $taskToolsRoot $taskVersion
$taskCompiler = Join-Path $taskToolset 'bin\Hostx64\x64\cl.exe'
if (-not [IO.File]::Exists($taskCompiler)) { throw "MSVC compiler is missing: $taskCompiler" }

# KitsRoot10 is required because the Windows SDK can be installed on another
# drive. Never assume that Build Tools and the SDK share the system drive.
$taskSdkRoot = $env:WindowsSdkDir
if ([string]::IsNullOrWhiteSpace($taskSdkRoot)) {
    $key = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows Kits\Installed Roots' -ErrorAction SilentlyContinue
    if ($key -and $key.KitsRoot10) { $taskSdkRoot = [string]$key.KitsRoot10 }
}
if ([string]::IsNullOrWhiteSpace($taskSdkRoot)) { $taskSdkRoot = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits\10' }
$taskSdkRoot = [IO.Path]::GetFullPath($taskSdkRoot).TrimEnd('\', '/')
$taskSdkVersion = $env:WindowsSDKVersion
if ([string]::IsNullOrWhiteSpace($taskSdkVersion)) {
    $sdkVersions = Get-ChildItem -LiteralPath (Join-Path $taskSdkRoot 'Include') -Directory -ErrorAction SilentlyContinue | Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName 'um\windows.h') }
    if (-not $sdkVersions) { throw "No Windows SDK headers were found under $taskSdkRoot" }
    $taskSdkVersion = ($sdkVersions | Sort-Object Name -Descending | Select-Object -First 1).Name + '\'
}
$taskSdkVersion = $taskSdkVersion.Trim('\', '/')
$taskSdkInclude = Join-Path $taskSdkRoot "Include\$taskSdkVersion"
$taskSdkLib = Join-Path $taskSdkRoot "Lib\$taskSdkVersion"
$taskVcInclude = Join-Path $taskToolset 'include'
$taskVcLib = Join-Path $taskToolset 'lib\x64'
foreach ($path in @("$taskSdkInclude\ucrt\stdio.h", "$taskSdkInclude\um\windows.h", "$taskSdkInclude\shared\ws2def.h", "$taskVcLib\libcmt.lib", "$taskSdkLib\ucrt\x64\libucrt.lib", "$taskSdkLib\um\x64\kernel32.lib")) {
    if (-not [IO.File]::Exists($path)) { throw "Windows SDK x64 component is missing: $path" }
}
$toolBin = Join-Path $taskToolset 'bin\Hostx64\x64'
$env:PATH = "$toolBin;$env:PATH"
$env:INCLUDE = "$taskVcInclude;$taskSdkInclude\ucrt;$taskSdkInclude\um;$taskSdkInclude\shared;$env:INCLUDE"
$env:LIB = "$taskVcLib;$taskSdkLib\ucrt\x64;$taskSdkLib\um\x64;$env:LIB"

$temporaryDll = Join-Path $taskIntermediate 'LocalUidSDK.dll'
$temporaryObj = Join-Path $taskIntermediate 'LocalUidSDK.obj'
$temporaryLib = Join-Path $taskIntermediate 'LocalUidSDK.lib'
$arguments = @(
    # The SDK intentionally reads process environment variables; suppress only
    # the CRT deprecation diagnostic, while /WX keeps every other warning fatal.
    '/nologo', '/LD', '/MT', '/O2', '/utf-8', '/D_CRT_SECURE_NO_WARNINGS', '/W4', '/WX', '/permissive-', '/Brepro',
    "/Fo$temporaryObj", "$taskSnapshot\LocalUidSDK.cpp",
    '/link', "/DEF:$taskSnapshot\sdk.def", "/OUT:$temporaryDll", "/IMPLIB:$temporaryLib", '/INCREMENTAL:NO', '/MACHINE:X64', 'Ws2_32.lib'
)
& $taskCompiler @arguments
if ($LASTEXITCODE -ne 0) {
    if (Test-Path -LiteralPath (Join-Path $taskOutput 'LocalUidSDK.dll')) { Remove-Item -LiteralPath (Join-Path $taskOutput 'LocalUidSDK.dll') -Force }
    throw 'SDK compilation/linking failed; no previous DLL was retained as a successful build.'
}
if (-not [IO.File]::Exists($temporaryDll)) { throw 'MSVC returned success but did not produce LocalUidSDK.dll.' }
[IO.File]::Copy($temporaryDll, (Join-Path $taskOutput 'LocalUidSDK.dll'), $true)

$inputHashes = [ordered]@{}
foreach ($name in $taskInputs) {
    $hash = [Security.Cryptography.SHA256]::Create()
    try {
        $stream = [IO.File]::OpenRead((Join-Path $taskSnapshot $name))
        try { $inputHashes[$name] = [BitConverter]::ToString($hash.ComputeHash($stream)).Replace('-', '').ToLowerInvariant() }
        finally { $stream.Dispose() }
    } finally { $hash.Dispose() }
}
$buildInfo = [ordered]@{
    inputs = $inputHashes
    visualStudio = $taskInstall
    msvcVersion = $taskVersion
    compilerVersion = (Get-Item -LiteralPath $taskCompiler).VersionInfo.ProductVersion
    windowsSdk = $taskSdkVersion
    linkerFlags = @('/Brepro', '/INCREMENTAL:NO', '/MACHINE:X64')
}
[IO.File]::WriteAllText((Join-Path $taskIntermediate 'sdk-inputs.json'), ($buildInfo | ConvertTo-Json -Depth 4), [Text.UTF8Encoding]::new($false))
Write-Host "LocalUidSDK.dll -> $taskOutput (MSVC $taskVersion, Windows SDK $taskSdkVersion, /Brepro)"
