[CmdletBinding()]
param(
    [string]$LauncherPath
)

$ErrorActionPreference = 'Stop'
if (-not $LauncherPath) {
    $LauncherPath = Join-Path (Split-Path -Parent $PSScriptRoot) 'client\launcher.exe'
}
if (-not (Test-Path -LiteralPath $LauncherPath)) {
    throw '找不到客户端。请用 -LauncherPath 指定《开放空间》的安装目录或 launcher.exe。'
}
$launcher = Get-Item -LiteralPath $LauncherPath
if ($launcher.PSIsContainer -or $launcher.Name -eq 'OverField.exe') {
    $installDir = if ($launcher.PSIsContainer) { $launcher.FullName } else { $launcher.DirectoryName }
    $candidates = @(
        (Join-Path $installDir 'launcher.exe')
        (Join-Path $installDir 'OverField Game\launcher.exe')
    )
    $games = @($candidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf })
    if ($games.Count -ne 1) {
        throw '安装目录中没有找到唯一的游戏程序。请用 -LauncherPath 指定实际游戏目录里的 launcher.exe。'
    }
    $launcher = Get-Item -LiteralPath $games[0]
}
if ($launcher.Name -ne 'launcher.exe') {
    throw '请选择《开放空间》游戏目录里的 launcher.exe。'
}

$plugins = Join-Path $launcher.DirectoryName 'launcher_Data\Plugins'
$executables = @($launcher.FullName)
foreach ($relative in @('ZFGameBrowser.exe', 'Win\PCSDK_release\PCSDKUI.exe', 'Win\PCSDK_release\QtWebEngineProcess.exe')) {
    $helper = Join-Path $plugins $relative
    if (Test-Path -LiteralPath $helper -PathType Leaf) {
        $executables += (Get-Item -LiteralPath $helper).FullName
    }
}
if ($executables -match '[,@]') {
    throw '游戏路径里的逗号或 @ 与 mitmproxy 的进程筛选语法冲突。请选择路径不含这些字符的游戏目录。'
}

$globalProxy = Get-CimInstance Win32_Process -Filter "Name = 'mitmdump.exe'" |
    Where-Object { $_.CommandLine -match '(?i)(?:^|\s)(?:--mode[=\s]+|-m\s+)"?local"?(?=\s|$)' } |
    Select-Object -First 1
if ($globalProxy) {
    throw '旧的全局 mitmdump 还在运行。请先回到旧转发窗口按 Ctrl+C，再启动本脚本。'
}

$mitmdump = Get-Command -Name mitmdump -CommandType Application -ErrorAction SilentlyContinue
if (-not $mitmdump) {
    throw '找不到 mitmdump。请先安装 mitmproxy，或在已有 mitmproxy 的 Python 环境中运行。'
}
$redirect = Join-Path $PSScriptRoot 'Redirect.py'
$mode = 'local:' + ($executables -join ',')
$showDetails = [bool]$PSBoundParameters['Verbose']
$flowDetail = if ($showDetails) { 1 } else { 0 }
$verbosity = if ($showDetails) { 'debug' } else { 'warn' }

Write-Host "准备为 $($launcher.FullName) 启动转发。按 Ctrl+C 结束。"
& $mitmdump.Source --mode $mode --flow-detail $flowDetail --set "termlog_verbosity=$verbosity" -s $redirect
exit $LASTEXITCODE
