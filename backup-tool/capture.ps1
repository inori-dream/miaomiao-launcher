[CmdletBinding()]
param(
    [ValidateSet('Account', 'Gardens')]
    [string]$Mode = 'Account',
    [string]$OutDir,
    [ValidateRange(1, 65535)]
    [int[]]$Ports = @(11001, 11002, 11003, 11004, 21001, 21002),
    [string[]]$ServerIps = @(),
    [ValidateRange(0, 3600)]
    [int]$AutoStopSeconds = 0,
    [switch]$CleanupOnly,
    [string]$StopFlag,
    [string]$ReadyPath,
    [string]$PythonExe,
    [string]$TSharkExe
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$OutputEncoding = [Text.UTF8Encoding]::new($false)
if (-not $PSBoundParameters.ContainsKey('OutDir')) {
    $OutDir = Join-Path $PSScriptRoot 'output'
}
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$user = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $user.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw '请右键对应的 .cmd 文件，选择「以管理员身份运行」。'
}

$python = if ($PythonExe) { $PythonExe } elseif ($env:OVERFIELD_BACKUP_PYTHON) { $env:OVERFIELD_BACKUP_PYTHON } else { Join-Path $PSScriptRoot 'tools\python\python.exe' }
$tshark = if ($TSharkExe) { $TSharkExe } elseif ($env:OVERFIELD_TSHARK) { $env:OVERFIELD_TSHARK } else { Join-Path $PSScriptRoot 'tools\tshark\tshark.exe' }
$env:OVERFIELD_TSHARK = $tshark
$pktmon = Join-Path $env:SystemRoot 'System32\PktMon.exe'
$programs = if ($CleanupOnly) { $pktmon } else { $python, $tshark, $pktmon }
foreach ($program in $programs) {
    if (-not (Test-Path -LiteralPath $program -PathType Leaf)) {
        throw "工具文件不完整，无法启动：找不到 $program 。请把压缩包全部解压到独立文件夹，保留 tools 目录；也可通过 PythonExe/TSharkExe 指定已有依赖。PktMon 需要受支持的 Windows。不会自动安装或下载。"
    }
}
if (-not $CleanupOnly) {
    & $python -B -c 'import cramjam, google.protobuf'
    if ($LASTEXITCODE -ne 0) { throw '内置 Python 依赖加载失败，请重新解压工具包。' }
    & $tshark --version | Out-Null
    if ($LASTEXITCODE -ne 0) { throw '内置 TShark 无法启动，请检查是否完整解压。' }
}

function Show-CaptureGuide {
    Write-Host '新手三步：①完整解压工具 ②看到「抓包已开始」再登录所选公服 ③完成浏览后结束并等待导出。'
    Write-Host '账号采集请先关闭私服转发工具，再退出并重启所选客户端；不要在压缩包预览目录中运行。'
    if ($Mode -eq 'Account') {
        Write-Host '登录进入场景后，依次打开角色与穿搭、背包、装备、任务、宠物、家园、好友、邮件、抽卡记录和图鉴。'
        Write-Host '家园预设请逐套切换并进入家园，等家具加载；未浏览的数据可能不会发送。'
    } else {
        Write-Host '请拜访要保存的好友家园，等家具加载出来后再进入下一家。'
    }
    if ($StopFlag) {
        Write-Host 'GUI 模式请使用启动器的结束采集按钮；不要直接关闭窗口。'
    } else {
        Write-Host 'CLI 模式请回到此窗口按回车结束；结束后需等待转换与导出完成。'
    }
    Write-Host "本轮结果目录：$sessionPath"
    Write-Host '采集文件和导出结果含账号隐私；不要公开分享原始抓包或 ZIP。'
}

function Invoke-PacketMonitor([string[]]$CommandArgs) {
    # PktMon emits UTF-8 even on a GBK PowerShell 5.1 host. Native '&' decoding
    # can corrupt the idle-state text and falsely report an unknown active session.
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $pktmon
    $info.Arguments = (@($CommandArgs | ForEach-Object {
        '"' + [regex]::Replace([regex]::Replace($_, '(\\*)"', '$1$1\"'), '(\\+)$', '$1$1') + '"'
    })) -join ' '
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.StandardOutputEncoding = [Text.UTF8Encoding]::new($false)
    $info.StandardErrorEncoding = [Text.UTF8Encoding]::new($false)
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $info
    try {
        [void]$process.Start()
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        $process.WaitForExit()
        $text = $stdout.Result
        $errorText = $stderr.Result
        if ($process.ExitCode -ne 0) {
            throw "PktMon $($CommandArgs[0]) 失败（退出码 $($process.ExitCode)）。$errorText $text"
        }
        $text -split '[\r\n]+' | Where-Object { $_ -ne '' }
    } finally { $process.Dispose() }
}

function Get-PacketMonitorFilterState {
    $lines = @(Invoke-PacketMonitor @('filter', 'list'))
    $rows = @($lines | Where-Object { $_ -match '^\s*\d' })
    if ($rows.Count -eq 0 -and ($lines -join "`n") -match '(?im)^\s*(none|无|無|no (packet )?filters( configured)?)\s*[.。]?\s*$') {
        return 'Empty'
    }
    # Accept both our own port filters and the address filters used by channels
    # whose game ports are decided at runtime.
    $own = 'OFBackup_(\d{1,5})\s+TCP\s+\1\s*$|OFBackup_ip_[\w.]+\s+TCP\s+'
    if ($rows.Count -gt 0 -and @($rows -notmatch $own).Count -eq 0) {
        return 'Backup'
    }
    return 'Other'
}

function Remove-BackupFilters {
    switch (Get-PacketMonitorFilterState) {
        'Backup' { Invoke-PacketMonitor @('filter', 'remove') | Out-Null }
        'Other' { throw '发现其他 PktMon 筛选器，或筛选器格式无法识别。请先在对应的诊断工具中清理。' }
    }
}

function Clear-PreviousBackupCapture {
    $status = (Invoke-PacketMonitor @('status')) -join [Environment]::NewLine
    $idlePattern = '(?im)^\s*(?:Packet\s+Monitor\s+is\s+not\s+running|数据包监视器\s*(?:没有|未在|未)运行|' +
        '封包監視器\s*(?:未|未在|沒有)(?:執行|運行))[.。]?\s*$'
    if ($status -notmatch $idlePattern) {
        $backupLogPattern = '(?im)^[^:：\r\n]+[:：][ \t]*"?' +
            '(?<etl>(?:[a-z]:|\\\\[^\\\r\n]+\\[^\\\r\n]+)(?:\\[^\\\r\n"]+)*' +
            '\\(?:account|gardens)_\d{8}_\d{6}_\d{3}\\(?:capture|segment_\d+)\.etl)"?[ \t]*\r?$'
        $logMatch = [regex]::Match($status, $backupLogPattern)
        if (-not $logMatch.Success -or (Get-PacketMonitorFilterState) -ne 'Backup') {
            throw "无法确认 PktMon 会话属于本备份工具。请在原诊断窗口结束会话。当前状态 $status"
        }
        $previousEtl = $logMatch.Groups['etl'].Value
        Invoke-PacketMonitor @('stop') | Out-Null
        if (Test-Path -LiteralPath $previousEtl -PathType Leaf) {
            Write-Host "已停止上一轮备份抓包。原始文件保存在 $previousEtl"
        } else {
            Write-Host "已停止上一轮残留备份抓包（原文件已不存在：$previousEtl）。"
        }
    }
    Remove-BackupFilters
}

$prefix = if ($Mode -eq 'Account') { 'account' } else { 'gardens' }
$sessionName = '{0}_{1}' -f $prefix, (Get-Date -Format 'yyyyMMdd_HHmmss_fff')
$sessionPath = [IO.Path]::GetFullPath((Join-Path $OutDir $sessionName))
$etl = Join-Path $sessionPath 'capture.etl'
$pcap = Join-Path $sessionPath 'capture.pcapng'
$export = Join-Path $sessionPath 'export'

$mutex = New-Object Threading.Mutex($false, 'Global\OverFieldBackupCapture')
$ownsMutex = $false
$started = $false
$filtersAdded = $false
$liveWorker = $null
$liveStop = Join-Path $sessionPath 'live-progress-stop.flag'
try {
    try { $ownsMutex = $mutex.WaitOne(0) }
    catch [Threading.AbandonedMutexException] { $ownsMutex = $true }
    if (-not $ownsMutex) { throw '另一个备份窗口正在抓包，请在那个窗口先按回车结束。' }

    Clear-PreviousBackupCapture
    if ($CleanupOnly) {
        Write-Host 'PktMon 已空闲。'
        return
    }

    New-Item -ItemType Directory -Path $sessionPath | Out-Null
    foreach ($port in ($Ports | Sort-Object -Unique)) {
        Invoke-PacketMonitor @('filter', 'add', "OFBackup_$port", '-t', 'TCP', '-p', "$port") | Out-Null
        $filtersAdded = $true
    }
    # Channels whose game ports are assigned at runtime (for example the Steam
    # build) cannot be captured by port. Fall back to the server address instead,
    # which the client does publish in its own config. A single comma-joined
    # argument binds as one string, so split it back into addresses.
    $addresses = @($ServerIps | ForEach-Object { "$_" -split ',' } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    foreach ($address in ($addresses | Sort-Object -Unique)) {
        $label = 'OFBackup_ip_' + ($address -replace '[^0-9A-Za-z]', '_')
        Invoke-PacketMonitor @('filter', 'add', $label, '-t', 'TCP', '-i', $address) | Out-Null
        $filtersAdded = $true
    }
    if (-not $StopFlag) {
        Invoke-PacketMonitor @('start', '--capture', '--comp', 'nics', '--pkt-size', '0', '--file-size', '1024', '--file-name', $etl) | Out-Null
        $started = $true
        if ($ReadyPath) {
            $readyData = @{ ready=$true; session=$sessionPath; pid=$PID } | ConvertTo-Json -Compress
            [IO.File]::WriteAllText($ReadyPath + '.tmp', $readyData, [Text.UTF8Encoding]::new($false))
            Move-Item -LiteralPath ($ReadyPath + '.tmp') -Destination $ReadyPath -Force
        }
        Write-Host "抓包已开始。输出目录 $sessionPath"
        Show-CaptureGuide
        if ($AutoStopSeconds -gt 0) {
            Start-Sleep -Seconds $AutoStopSeconds
        } else {
            Read-Host '采集完成后，请回到本窗口按回车键结束抓包并自动导出' | Out-Null
        }
        Invoke-PacketMonitor @('stop') | Out-Null
        $started = $false
        Invoke-PacketMonitor @('etl2pcap', $etl, '--out', $pcap) | Out-Null
    } else {
        # PktMon buffers events until the trace stops, so a single long trace stays
        # empty on disk until the very end. Rotate short traces instead: each stop
        # flushes a finished segment, and the segments are concatenated into one
        # pcapng (multi-section) so the live checklist can read real data.
        $segmentSeconds = 12
        $segment = 0
        $first = $true
        while ($true) {
            $segment++
            $segmentEtl = Join-Path $sessionPath ("segment_{0:d3}.etl" -f $segment)
            Invoke-PacketMonitor @('start', '--capture', '--comp', 'nics', '--pkt-size', '0', '--file-size', '1024', '--file-name', $segmentEtl) | Out-Null
            $started = $true
            if ($first) {
                $first = $false
                if ($ReadyPath) {
                    $readyData = @{ ready=$true; session=$sessionPath; pid=$PID } | ConvertTo-Json -Compress
                    [IO.File]::WriteAllText($ReadyPath + '.tmp', $readyData, [Text.UTF8Encoding]::new($false))
                    Move-Item -LiteralPath ($ReadyPath + '.tmp') -Destination $ReadyPath -Force
                }
                Write-Host "抓包已开始。输出目录 $sessionPath"
                $observer = Join-Path (Split-Path $PSScriptRoot -Parent) 'capture_checklist.py'
                if ($Mode -eq 'Account' -and $StopFlag -and (Test-Path -LiteralPath $observer)) {
                    $liveDir = Join-Path $sessionPath 'live-progress'
                    New-Item -ItemType Directory -Path $liveDir -Force | Out-Null
                    try {
                        $arguments = '-B -X utf8 "' + $observer + '" watch --capture-root "' + $sessionPath + '"'
                        $liveWorker = Start-Process -FilePath $python -ArgumentList $arguments -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $liveDir 'observer.log') -RedirectStandardError (Join-Path $liveDir 'observer.err.log')
                        # Persist the identity of the helper we just started. The
                        # launcher's cleanup reaps only processes backed by such a
                        # record, so a user's own capture_checklist run is never
                        # stopped by a command-line match alone.
                        $liveIdentity = @{
                            Id      = [int]$liveWorker.Id
                            Path    = $python
                            Started = $liveWorker.StartTime.ToUniversalTime().ToString('o')
                            Owned   = $true
                        } | ConvertTo-Json -Compress
                        [IO.File]::WriteAllText((Join-Path $liveDir 'helper-record.json'), $liveIdentity, [Text.UTF8Encoding]::new($false))
                    } catch { Write-Host '实时清单暂不可用，结束后仍会核对完整产物。' }
                }
                Show-CaptureGuide
            }
            $offset = 0
            while ($offset -lt $segmentSeconds) {
                if ($AutoStopSeconds -gt 0 -and ($segment - 1) * $segmentSeconds + $offset -ge $AutoStopSeconds) { break }
                if ($StopFlag -and (Test-Path -LiteralPath $StopFlag)) { break }
                Start-Sleep -Milliseconds 500
                $offset += 0.5
            }
            Invoke-PacketMonitor @('stop') | Out-Null
            $started = $false
            if (Test-Path -LiteralPath $segmentEtl -PathType Leaf) {
                $segmentPcap = Join-Path $sessionPath ("segment_{0:d3}.pcapng" -f $segment)
                Invoke-PacketMonitor @('etl2pcap', $segmentEtl, '--out', $segmentPcap) | Out-Null
                if ((Test-Path -LiteralPath $segmentPcap) -and (Get-Item -LiteralPath $segmentPcap).Length -gt 256) {
                    # Appending pcapng sections keeps every segment readable by TShark.
                    $bytes = [IO.File]::ReadAllBytes($segmentPcap)
                    $stream = [IO.File]::Open($pcap, 'Append', 'Write', 'ReadWrite')
                    try { $stream.Write($bytes, 0, $bytes.Length) } finally { $stream.Dispose() }
                }
            }
            $done = $AutoStopSeconds -gt 0 -and ($segment * $segmentSeconds) -ge $AutoStopSeconds
            $stopped = $StopFlag -and (Test-Path -LiteralPath $StopFlag)
            if ($done -or $stopped) { break }
        }
    }
} finally {
    if (Test-Path -LiteralPath $sessionPath) {
        New-Item -ItemType File -Path $liveStop -Force | Out-Null
        if ($liveWorker) { [void]$liveWorker.WaitForExit(2000); $liveWorker.Dispose() }
    }
    try {
        if ($started) { Invoke-PacketMonitor @('stop') | Out-Null }
        if ($filtersAdded) { Remove-BackupFilters }
    } finally {
        if ($ownsMutex) { $mutex.ReleaseMutex() }
        $mutex.Dispose()
    }
}

if (-not (Test-Path -LiteralPath $pcap -PathType Leaf)) {
    $segments = @(Get-ChildItem -LiteralPath $sessionPath -Filter 'segment_*.pcapng' -ErrorAction SilentlyContinue)
    if ($segments.Count -eq 0) { throw "没有采集到任何数据包：请确认登录公服后确实进入了游戏场景。" }
    throw "找不到合并后的抓包文件 $pcap"
}
$scriptName = if ($Mode -eq 'Account') { 'parse_pcap.py' } else { 'extract_gardens.py' }
$env:PYTHONUTF8 = '1'
& $python -B -X utf8 (Join-Path $PSScriptRoot $scriptName) $pcap --out $export
if ($LASTEXITCODE -ne 0) {
    throw "解包失败。可从 $pcap 重新解包。"
}
Write-Host "采集和导出已完成。结果目录：$sessionPath"
Write-Host "原始抓包：$pcap"
Write-Host "解析结果：$export"
Write-Host '请在启动器的结果页面查看/打开本轮目录或导出的 ZIP；CLI 本身不会额外打包 ZIP。'
Write-Host '若数据不全，可保留原始抓包后重新解析，或重新采集并补齐页面浏览。'
