[CmdletBinding()]
param(
    [ValidateSet('Start','Stop','Status','Backup','Cleanup')][string]$Action = 'Status',
    [switch]$OrphansOnly,
    [string]$GameRoot = '',
    [ValidateSet('solo','host','steam')][string]$Mode = 'solo',
    [uint32]$Uid = 1000000,
    [string]$HostAddress = '127.0.0.1',
    [int]$ProxyPort = 18890,
    [switch]$LaunchClient, [switch]$StopServer, [switch]$Json,
    [string]$PythonExe, [string]$ResultPath
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$taskAppRoot = $PSScriptRoot
$taskInstallRoot = if ((Split-Path -Leaf $taskAppRoot) -ieq 'app') { Split-Path -Parent $taskAppRoot } else { $taskAppRoot }
if([string]::IsNullOrWhiteSpace($GameRoot)) { $GameRoot=$taskInstallRoot }
$taskBase = Join-Path $taskInstallRoot 'profiles'
$taskStateFile = Join-Path $taskInstallRoot "runtime\world-state-$Mode.json"
# Older builds kept a single shared state file. Read it once so an upgrade keeps
# stopping what the previous launcher started.
$taskLegacyStateFile = Join-Path $taskInstallRoot 'runtime\world-state.json'
$taskRegistry = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings'
$taskProfile = Join-Path $taskBase $Mode
$taskExecutable = Join-Path $taskProfile 'server\Lolo.exe'
$taskClient = Join-Path $taskProfile 'client\launcher.exe'
function Find-Client([string]$Base) {
    if ([string]::IsNullOrWhiteSpace($Base)) { return $null }
    foreach ($candidate in @(
        (Join-Path $Base 'launcher.exe'),
        (Join-Path $Base 'OverField Game\launcher.exe')
    )) { if (Test-Path -LiteralPath $candidate) { return $candidate } }
    foreach ($child in (Get-ChildItem -LiteralPath $Base -Directory -ErrorAction SilentlyContinue)) {
        foreach ($candidate in @(
            (Join-Path $child.FullName 'launcher.exe'),
            (Join-Path $child.FullName 'OverField Game\launcher.exe')
        )) { if (Test-Path -LiteralPath $candidate) { return $candidate } }
    }
    return $null
}
$taskOriginalClient = Find-Client $GameRoot
$taskTarget = if ($Mode -eq 'host') { $HostAddress } else { '127.0.0.1' }
# One disjoint port block per mode: the national and the Steam world can run at
# the same time, each with its own server, gateway and admin port.
$taskHttp = switch ($Mode) { 'solo' { 18080 } 'host' { 19080 } 'steam' { 20080 } }
$taskGateway = switch ($Mode) { 'solo' { 18100 } 'host' { 19100 } 'steam' { 20100 } }
$taskLogPort = switch ($Mode) { 'solo' { 18200 } 'host' { 19200 } 'steam' { 20200 } }
$taskAdminPort = switch ($Mode) { 'solo' { 18300 } 'host' { 19300 } 'steam' { 20300 } }

function Read-StateFile([string]$File) {
    $taskText = [IO.File]::ReadAllText($File)
    $taskRead = $taskText | ConvertFrom-Json -ErrorAction Stop
    if ($taskRead -isnot [pscustomobject]) { throw 'World state must be a JSON object.' }
    return $taskRead
}
function Preserve-CorruptState([string]$File) {
    [IO.Directory]::CreateDirectory((Split-Path -Parent $File)) | Out-Null
    $taskEvidence = "$File.corrupt-$([DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss-fff'))-$([Guid]::NewGuid().ToString('N')).json"
    try {
        [IO.File]::Move($File,$taskEvidence)
    } catch [IO.IOException] {
        # A scanner or another process may briefly hold the corrupt file open.
        [IO.File]::Copy($File,$taskEvidence,$true)
        [IO.File]::Delete($File)
    }
    return $taskEvidence
}
function Read-State {
    # Each mode keeps its own record so starting the Steam world never hides the
    # national world's processes. Fall back to the old shared file once.
    $taskFile = $taskStateFile
    if (-not (Test-Path -LiteralPath $taskFile) -and (Test-Path -LiteralPath $taskLegacyStateFile)) {
        $taskFile = $taskLegacyStateFile
    }
    if (Test-Path -LiteralPath $taskFile) {
        try { $taskRead = Read-StateFile $taskFile }
        catch {
            $taskCorrupt = Preserve-CorruptState $taskFile
            $taskBackup = "$taskFile.bak"
            if (-not (Test-Path -LiteralPath $taskBackup)) { throw "World state is corrupt and no backup exists. Evidence: $taskCorrupt" }
            try { $taskRead = Read-StateFile $taskBackup }
            catch { throw "World state and backup are corrupt. Evidence: $taskCorrupt" }
            # The corrupt primary was retained under a unique name. Re-publish
            # the known-good backup atomically without overwriting that backup.
            Save-State $taskRead
        }
        if ($taskFile -eq $taskStateFile -and -not $taskRead.PSObject.Properties['Mode']) { throw 'This mode state file has no Mode identity.' }
        if (-not $taskRead.PSObject.Properties['Gateway']) { $taskRead | Add-Member -NotePropertyName Gateway -NotePropertyValue $null }
        if (-not $taskRead.PSObject.Properties['Python']) { $taskRead | Add-Member -NotePropertyName Python -NotePropertyValue '' }
        if (-not $taskRead.PSObject.Properties['Server']) { $taskRead | Add-Member -NotePropertyName Server -NotePropertyValue $null }
        if (-not $taskRead.PSObject.Properties['Proxy']) { $taskRead | Add-Member -NotePropertyName Proxy -NotePropertyValue $null }
        if (-not $taskRead.PSObject.Properties['Mode']) { $taskRead | Add-Member -NotePropertyName Mode -NotePropertyValue 'solo' }
        if ($taskFile -eq $taskStateFile -and $taskRead.Mode -ne $Mode) { throw 'This state file belongs to another mode; it was not adopted.' }
        # A legacy state belonging to another mode must never be adopted or stopped.
        if ($taskRead.Mode -ne $Mode) { return $null }
        return $taskRead
    }
    return $null
}
function Save-State($State) {
    $taskJson = $State | ConvertTo-Json -Depth 12
    [IO.Directory]::CreateDirectory((Split-Path -Parent $taskStateFile)) | Out-Null
    $taskTemporary = "$taskStateFile.$([Guid]::NewGuid().ToString('N')).tmp"
    $taskBackup = "$taskStateFile.bak"
    try {
        [IO.File]::WriteAllText($taskTemporary, $taskJson, [Text.UTF8Encoding]::new($false))
        if (Test-Path -LiteralPath $taskStateFile) {
            [IO.File]::Replace($taskTemporary, $taskStateFile, $taskBackup)
        } else {
            [IO.File]::Move($taskTemporary, $taskStateFile)
        }
    } finally {
        if (Test-Path -LiteralPath $taskTemporary) { [IO.File]::Delete($taskTemporary) }
    }
}
function Test-Port([string]$Address, [int]$Port) {
    $taskSocket = [Net.Sockets.TcpClient]::new()
    try { $taskConnect=$taskSocket.ConnectAsync($Address,$Port); return $taskConnect.Wait(800) -and $taskSocket.Connected }
    catch { return $false } finally { $taskSocket.Dispose() }
}
function Test-PortOwner([string]$Address,[int]$Port,$Record) {
    if (-not (Process-Matches $Record)) { return $false }
    try { $taskListeners=@(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction Stop) }
    catch { return $false }
    foreach ($taskListener in $taskListeners) {
        if ($taskListener.OwningProcess -eq $Record.Id) { return $true }
    }
    return $false
}
function Test-FirewallRules([string[]]$Names) {
    # Get-NetFirewallRule costs about a second per call, which stalls every status
    # refresh in host mode. The stored rule blobs expose the same Enabled flag.
    $path = 'HKLM:\SYSTEM\CurrentControlSet\Services\SharedAccess\Parameters\FirewallPolicy\FirewallRules'
    try { $rules = Get-ItemProperty -LiteralPath $path -ErrorAction Stop } catch { return $false }
    $enabled = 0
    foreach ($name in $Names) {
        $rule = $rules.PSObject.Properties | Where-Object { $_.Name -eq $name } | Select-Object -First 1
        if ($rule -and ([string]$rule.Value) -match '\|Active=TRUE(\||$)') { $enabled++ }
    }
    return $enabled -eq $Names.Count
}
function Test-Ports([object[]]$Targets, [int]$TimeoutMilliseconds = 250) {
    # A closed port always burns the full timeout, so probe all of them at once
    # instead of paying that cost three times in a row.
    $sockets = @()
    $tasks = @()
    foreach ($target in $Targets) {
        $socket = [Net.Sockets.TcpClient]::new()
        $sockets += $socket
        try { $tasks += ,$socket.ConnectAsync([string]$target[0], [int]$target[1]) }
        catch { $tasks += ,$null }
    }
    # WaitHandle.WaitAll is rejected on PowerShell's STA thread. Wait each
    # handle against a shared deadline so closed ports still share one timeout.
    $deadline = [DateTime]::UtcNow.AddMilliseconds($TimeoutMilliseconds)
    foreach ($task in $tasks) {
        if ($null -eq $task) { continue }
        $remaining = [int][Math]::Max(0, ($deadline - [DateTime]::UtcNow).TotalMilliseconds)
        try { $task.AsyncWaitHandle.WaitOne($remaining) | Out-Null } catch { }
    }
    $results = @()
    for ($i = 0; $i -lt $sockets.Count; $i++) {
        $results += (($null -ne $tasks[$i]) -and $sockets[$i].Connected)
    }
    foreach ($socket in $sockets) { try { $socket.Dispose() } catch { } }
    return $results
}
function Test-OwnedRecord($Record) {
    if (-not $Record -or $Record.Owned -ne $true) { return $false }
    if ($Record.Id -isnot [int] -and $Record.Id -isnot [long]) { return $false }
    if ([int]$Record.Id -le 0 -or [string]::IsNullOrWhiteSpace([string]$Record.Path)) { return $false }
    $hasTicks = $Record.PSObject.Properties['startedTicks'] -and ($Record.startedTicks -is [long] -or $Record.startedTicks -is [int])
    if ($hasTicks -and [long]$Record.startedTicks -gt 0) { return $true }
    if ([string]::IsNullOrWhiteSpace([string]$Record.Started)) { return $false }
    try { $null=[DateTimeOffset]::Parse([string]$Record.Started) } catch { return $false }
    return $true
}
function Process-Matches($Record,[string]$ExpectedPath) {
    if (-not (Test-OwnedRecord $Record)) { return $false }
    if ($ExpectedPath -and -not [string]::Equals([string]$Record.Path,$ExpectedPath,[StringComparison]::OrdinalIgnoreCase)) { return $false }
    try {
        $taskProcess = Get-Process -Id $Record.Id -ErrorAction Stop
        $taskActual=$taskProcess.StartTime.ToUniversalTime().Ticks
        $hasTicks = $Record.PSObject.Properties['startedTicks'] -and ($Record.startedTicks -is [long] -or $Record.startedTicks -is [int]) -and [long]$Record.startedTicks -gt 0
        if ($hasTicks) {
            # service_runner records Windows FILETIME (1601 epoch); .NET uses 0001.
            $taskExpected=[long]$Record.startedTicks + 504911232000000000
            $taskSameStart = ($taskExpected -eq $taskActual)
        } else {
            # Python emits microsecond ISO timestamps while .NET renders 100ns ticks.
            $taskExpected=[DateTimeOffset]::Parse([string]$Record.Started).UtcDateTime.Ticks
            $taskSameStart = ([Math]::Abs([double]($taskExpected-$taskActual)) -le 10)
        }
        return $taskProcess.Path -and [string]::Equals($taskProcess.Path,[string]$Record.Path,[StringComparison]::OrdinalIgnoreCase) -and $taskSameStart
    } catch { return $false }
}
function Stop-Descendants($RootRecord,[string]$ExpectedPath) {
    $taskAll=@(Get-CimInstance Win32_Process -ErrorAction Stop)
    $taskById=@{}
    foreach ($taskItem in $taskAll) { $taskById[[int]$taskItem.ProcessId]=$taskItem }
    if (-not $taskById.ContainsKey([int]$RootRecord.Id)) { throw 'The owned root process disappeared during process-tree cleanup.' }
    $taskRecords=@($RootRecord)
    $taskDepths=@{([int]$RootRecord.Id)=0}
    $taskQueue=[Collections.Generic.Queue[int]]::new()
    $taskQueue.Enqueue([int]$RootRecord.Id)
    while ($taskQueue.Count -gt 0) {
        $taskParent=$taskQueue.Dequeue()
        if ($taskDepths[$taskParent] -ge 64) { throw 'Owned process tree is unexpectedly deep.' }
        foreach ($taskItem in $taskAll) {
            if ([int]$taskItem.ParentProcessId -ne $taskParent) { continue }
            $taskId=[int]$taskItem.ProcessId
            if ($taskDepths.ContainsKey($taskId)) { continue }
            try { $taskProcess=Get-Process -Id $taskId -ErrorAction Stop } catch { continue }
            if (-not $taskProcess.Path) { throw "Could not verify child process $taskId executable path." }
            $taskChild=[pscustomobject]@{
                Id=$taskId; Path=$taskProcess.Path; Started=$taskProcess.StartTime.ToUniversalTime().ToString('o'); Owned=$true
            }
            # Confirm both PID+path+start time and a live, verified ancestry chain.
            if (-not (Process-Matches $taskChild)) { throw "Child process $taskId identity changed during cleanup." }
            for ($taskAncestor=$taskParent;$taskAncestor -ne $RootRecord.Id;$taskAncestor=[int]$taskById[$taskAncestor].ParentProcessId) {
                if (-not $taskById.ContainsKey($taskAncestor) -or -not (Process-Matches $taskRecords[$taskDepths[$taskAncestor]])) {
                    throw "Child process $taskId no longer has a verified owned ancestry chain."
                }
            }
            $taskDepths[$taskId]=$taskDepths[$taskParent]+1
            $taskRecords+=,$taskChild
            $taskQueue.Enqueue($taskId)
        }
    }
    $taskChildren=@($taskRecords | Where-Object { $_.Id -ne $RootRecord.Id } | Sort-Object -Property @{Expression={$taskDepths[[int]$_.Id]};Descending=$true})
    foreach ($taskChild in $taskChildren) {
        if (-not (Process-Matches $RootRecord $ExpectedPath)) { throw 'The owned root exited during process-tree cleanup.' }
        if (-not (Process-Matches $taskChild)) { continue }
        $taskProcess=Get-Process -Id $taskChild.Id -ErrorAction Stop
        Stop-Process -Id $taskProcess.Id -Force -ErrorAction Stop
        if (-not $taskProcess.WaitForExit(5000)) { throw "Timed out stopping child process $($taskChild.Id)." }
    }
}
function Stop-Recorded($Record,[string]$ExpectedPath) {
    if (-not $Record) { return }
    if (-not (Test-OwnedRecord $Record)) { throw 'Refusing to stop an incomplete or unowned process record.' }
    # A stale record or a PID recycled by a user process is a safe no-op.
    if (-not (Process-Matches $Record $ExpectedPath)) { return }
    # Re-enumerate immediately before stopping the root to catch children spawned
    # while an earlier descendant snapshot was being terminated.
    for ($taskPass=0;$taskPass -lt 4;$taskPass++) {
        Stop-Descendants $Record $ExpectedPath
        $taskRemaining=@(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object { [int]$_.ParentProcessId -eq [int]$Record.Id })
        if ($taskRemaining.Count -eq 0) { break }
    }
    if (-not (Process-Matches $Record $ExpectedPath)) { return }
    $taskProcess=Get-Process -Id $Record.Id -ErrorAction Stop
    Stop-Process -Id $taskProcess.Id -Force -ErrorAction Stop
    if (-not $taskProcess.WaitForExit(5000)) { throw 'Timed out stopping this profile process.' }
}
function Write-LifecycleEvidence($Operation,$Error,$State) {
    try {
        $taskRoot = if ($taskInstallRoot) { $taskInstallRoot } else { $PSScriptRoot }
        [IO.Directory]::CreateDirectory((Join-Path $taskRoot 'runtime')) | Out-Null
        $taskPath=Join-Path $taskRoot "runtime\lifecycle-error-$Mode-$([DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss-fff'))-$([Guid]::NewGuid().ToString('N')).json"
        $taskValue=@{ Operation=$Operation; Mode=$Mode; Error=[string]$Error; Created=[DateTime]::UtcNow.ToString('o'); State=$State } | ConvertTo-Json -Depth 12
        [IO.File]::WriteAllText($taskPath,$taskValue,[Text.UTF8Encoding]::new($false))
    } catch { }
}
function Notify-Proxy {
    if (-not ('WorldWinInet' -as [type])) {
        Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public static class WorldWinInet { [DllImport("wininet.dll")] public static extern bool InternetSetOption(IntPtr h,int o,IntPtr b,int n); }'
    }
    [WorldWinInet]::InternetSetOption([IntPtr]::Zero,39,[IntPtr]::Zero,0) | Out-Null
    [WorldWinInet]::InternetSetOption([IntPtr]::Zero,37,[IntPtr]::Zero,0) | Out-Null
}
function Restore-Route($State) {
    if (-not $State -or -not $State.ProxyBefore) { return }
    $taskCurrent=Get-ItemProperty -LiteralPath $taskRegistry
    foreach ($taskName in @('AutoConfigURL','ProxyEnable')) {
        $taskExpected = if ($taskName -eq 'AutoConfigURL') { $State.PacUrl } else { 0 }
        if ($taskCurrent.$taskName -ne $taskExpected) { continue }
        $taskBefore=$State.ProxyBefore.$taskName
        if ($taskBefore.Exists) {
            $taskKind=if ($taskName -eq 'ProxyEnable') { 'DWord' } else { 'String' }
            New-ItemProperty -LiteralPath $taskRegistry -Name $taskName -Value $taskBefore.Value -PropertyType $taskKind -Force | Out-Null
        } else { Remove-ItemProperty -LiteralPath $taskRegistry -Name $taskName -ErrorAction SilentlyContinue }
    }
    Notify-Proxy
}
function Stop-World($State) {
    if (-not $State) { return }
    $taskErrors=@()
    try { Restore-Route $State } catch { $taskErrors+="routing: $($_.Exception.Message)" }
    $taskGatewayPath=if ($State.Python) { [string]$State.Python } else { $taskPython }
    foreach ($taskRole in @('Proxy','Gateway','Server')) {
        $taskRecord=$State.$taskRole
        if (-not $taskRecord) { continue }
        $taskExpected=if ($taskRole -eq 'Server') { $taskExecutable } else { $taskGatewayPath }
        if ($taskRole -ne 'Server' -and -not $taskExpected) {
            $taskErrors+="$taskRole record has no verified Python executable path."
            continue
        }
        try { Stop-Recorded $taskRecord $taskExpected } catch { $taskErrors+="${taskRole}: $($_.Exception.Message)" }
    }
    if ($taskErrors.Count -gt 0) {
        $State.Active=$true
        $State.LastError=($taskErrors -join '; ')
        try { Save-State $State } catch { $taskErrors+="state: $($_.Exception.Message)" }
        Write-LifecycleEvidence 'Stop' ($taskErrors -join '; ') $State
        throw ('Could not stop every owned process safely: '+($taskErrors -join '; '))
    }
    $State.Active=$false
    $State | Add-Member -NotePropertyName LastStopped -NotePropertyValue ([DateTime]::UtcNow.ToString('o')) -Force
    Save-State $State
    Remove-LaunchRecord 'Server'
    Remove-LaunchRecord 'Gateway'
}
function Read-ErrorDetail([object[]]$Lines,[string]$LogDirectory='',[string]$Prefix='') {
    $cleaned = @($Lines | ForEach-Object { [string]$_ } | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | ForEach-Object { $_.Trim() })
    for ($i = $cleaned.Count - 1; $i -ge 0; $i--) {
        if ($cleaned[$i] -match '^\s*\{.*"error"\s*:') {
            try { $obj = $cleaned[$i] | ConvertFrom-Json -ErrorAction Stop; if ($obj.error) { return [string]$obj.error } } catch { }
        }
    }
    if ($cleaned.Count -gt 0) {
        $last = $cleaned[-1]
        if ($last -match '^(?:[A-Za-z_][A-Za-z0-9_]*(?:Error|Exception)):\s*(.+)$') { return $Matches[0] }
        return $last
    }
    if ($LogDirectory -and $Prefix -and (Test-Path -LiteralPath $LogDirectory)) {
        $latest = Get-ChildItem -LiteralPath $LogDirectory -Filter "$Prefix*.err.log" -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if ($latest) {
            $logLine = @(Get-Content -LiteralPath $latest.FullName -Tail 8 -Encoding UTF8 -ErrorAction SilentlyContinue | ForEach-Object { $_.Trim() } | Where-Object { $_ }) | Select-Object -Last 1
            if ($logLine) { return $logLine }
        }
    }
    return ''
}
function Invoke-PythonHelper([string[]]$Arguments) {
    $taskPrev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $taskRaw = @(& $taskPython @Arguments 2>&1)
    } finally {
        $ErrorActionPreference = $taskPrev
    }
    $taskOut = @($taskRaw | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] } | ForEach-Object { [string]$_ })
    $taskErr = @($taskRaw | Where-Object { $_ -is [System.Management.Automation.ErrorRecord] } | ForEach-Object { $_.ToString() })
    return [pscustomobject]@{ Code = $LASTEXITCODE; Out = $taskOut; Err = $taskErr }
}
function Python-Json([string[]]$Arguments) {
    $taskHelperRoot = if ($taskAppRoot) { $taskAppRoot } else { $PSScriptRoot }
    $taskRun = Invoke-PythonHelper (@('-X','utf8',(Join-Path $taskHelperRoot 'profile_manager.py')) + $Arguments)
    if ($taskRun.Code -ne 0) {
        $detail = Read-ErrorDetail (@($taskRun.Out) + @($taskRun.Err))
        if ($detail) { throw "Profile operation failed: $detail" }
        throw 'Profile operation failed. Check the game directory or LAN address.'
    }
    return ($taskRun.Out | Out-String) | ConvertFrom-Json
}
function Start-Background([string]$Executable,[string]$Directory,[string]$Name,[string[]]$Arguments,[string[]]$Environment=@()) {
    $taskRoot = if ($taskInstallRoot) { $taskInstallRoot } else { $PSScriptRoot }
    $taskHelperRoot = if ($taskAppRoot) { $taskAppRoot } else { $PSScriptRoot }
    $taskStamp=Get-Date -Format 'yyyyMMdd-HHmmss-fff'
    $taskLogDirectory=Join-Path $taskProfile 'logs'
    $taskPidPath=Join-Path $taskRoot "runtime\owned-$Mode-$Name.json"
    $taskArgs=@('-X','utf8',(Join-Path $taskHelperRoot 'service_runner.py'),'--executable',$Executable,'--cwd',$Directory,
        '--stdout',(Join-Path $taskLogDirectory "$Name-$taskStamp.log"),'--stderr',(Join-Path $taskLogDirectory "$Name-$taskStamp.err.log"),'--pid-path',$taskPidPath)
    foreach ($taskEnv in $Environment) { $taskArgs+=@('--env',$taskEnv) }
    $taskArgs+=@('--')+$Arguments
    $taskRun = Invoke-PythonHelper $taskArgs
    $taskOutput = $taskRun.Out
    if ($taskRun.Code -ne 0) {
        if (Test-Path -LiteralPath $taskPidPath) {
            $taskRecord=Read-LaunchRecord $taskPidPath
            Stop-Recorded $taskRecord $Executable
            [IO.File]::Delete($taskPidPath)
        }
        $detail = Read-ErrorDetail (@($taskRun.Out) + @($taskRun.Err)) $taskLogDirectory "$Name-"
        if ($detail) { throw "Could not start this profile service: $detail" }
        throw 'Could not start this profile service.'
    }
    try { $taskData=($taskOutput | Out-String | ConvertFrom-Json -ErrorAction Stop) } catch { throw 'Service launcher returned an invalid identity record.' }
    if ($taskData.error) { throw [string]$taskData.error }
    if ([string]::IsNullOrWhiteSpace([string]$taskData.path) -or [string]::IsNullOrWhiteSpace([string]$taskData.Started)) { throw 'Service launcher returned an incomplete identity record.' }
    if (-not [string]::Equals([string]$taskData.path,$Executable,[StringComparison]::OrdinalIgnoreCase)) { throw 'Service launcher returned the wrong executable path.' }
    # Use the identity produced by the launcher. Re-querying by PID here races
    # with an immediate child crash and can lose the only cleanup record.
    $taskOutRecord = [pscustomobject]@{ Id=[int]$taskData.pid; Path=[string]$taskData.path; Started=[string]$taskData.Started; Owned=$true }
    if ($taskData.PSObject.Properties['startedTicks'] -and $null -ne $taskData.startedTicks) {
        $taskOutRecord | Add-Member -NotePropertyName startedTicks -NotePropertyValue ([long]$taskData.startedTicks)
    }
    return $taskOutRecord
}
function Wait-Ready([string]$Address,[int]$Port,$Record,[int]$Attempts=80) {
    for ($taskAttempt=0;$taskAttempt -lt $Attempts;$taskAttempt++) {
        if (-not (Process-Matches $Record)) {
            $logDir = if ($taskProfile) { Join-Path $taskProfile 'logs' } else { '' }
            $detail = ''
            if ($logDir -and (Test-Path -LiteralPath $logDir)) {
                $latest = Get-ChildItem -LiteralPath $logDir -Filter '*.err.log' -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
                if ($latest) {
                    $detail = @(Get-Content -LiteralPath $latest.FullName -Tail 6 -Encoding UTF8 -ErrorAction SilentlyContinue | ForEach-Object { $_.Trim() } | Where-Object { $_ }) | Select-Object -Last 1
                }
            }
            if ($detail) { throw "Profile service exited: $detail. See this profile logs." }
            throw 'Profile service exited. See this profile logs.'
        }
        # Connecting is not enough: a process that raced us for the port must not
        # be mistaken for the service this task just started.
        if ((Test-Port $Address $Port) -and (Test-PortOwner $Address $Port $Record)) { return }
        Start-Sleep -Milliseconds 250
    }
    throw "Profile port $Port did not become ready or is owned by another process."
}
function Client-Running {
    return @(Get-Process -Name 'launcher' -ErrorAction SilentlyContinue | Where-Object { $_.Path -and [string]::Equals($_.Path,$taskClient,[StringComparison]::OrdinalIgnoreCase) }).Count -gt 0
}
function Stop-Orphans {
    # Ownership comes from a record written when the helper was started. Matching a
    # command line proves what a process is, not who started it, so a candidate
    # without a record is reported and left running: it may be the user's own
    # capture_checklist run.
    $stopped = @()
    $taskErrors=@()
    $taskUnverified=@()
    $taskRoot = if ($taskInstallRoot) { $taskInstallRoot } else { $PSScriptRoot }
    $taskHelperRoot = if ($taskAppRoot) { $taskAppRoot } else { $PSScriptRoot }
    $taskSessions=Join-Path $taskRoot 'runtime\capture-sessions'
    if (-not (Test-Path -LiteralPath $taskSessions)) { return $stopped }
    $taskRecords=@(Get-ChildItem -LiteralPath $taskSessions -Filter 'helper-record.json' -File -Recurse -ErrorAction SilentlyContinue)
    foreach ($taskFile in $taskRecords) {
        try {
            $taskRecord=Read-LaunchRecord $taskFile.FullName
            # The recorded helper must have been started by this installation's
            # Python; anything else is a tampered or stale record.
            if (-not (Process-Matches $taskRecord $taskRecord.Path)) {
                [IO.File]::Delete($taskFile.FullName)
                continue
            }
            Stop-Recorded $taskRecord $taskRecord.Path
            $stopped += [int]$taskRecord.Id
            [IO.File]::Delete($taskFile.FullName)
        } catch { $taskErrors+="helper record $($taskFile.FullName): $($_.Exception.Message)" }
    }
    # Report, never terminate, helpers that have no record.
    try {
        foreach ($taskCandidate in @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
            $taskCommand = $_.CommandLine
            if ([string]::IsNullOrWhiteSpace($taskCommand)) { return $false }
            # A helper this pass just stopped must not be reported as unowned.
            if ([int]$_.ProcessId -in $stopped) { return $false }
            ($_.Name -eq 'python.exe' -and $taskCommand -like "*$taskHelperRoot*capture_checklist.py*" -and $taskCommand -like '* watch *') -or
            ($_.Name -eq 'PktMon.exe' -and $taskCommand -like '*live-progress*snapshot*')
        })) {
            $taskUnverified += [int]$taskCandidate.ProcessId
        }
    } catch { }
    if ($taskUnverified.Count -gt 0) {
        Write-Output ("Left {0} helper process(es) running because no ownership record exists: {1}" -f $taskUnverified.Count, ($taskUnverified -join ', '))
    }
    if ($taskErrors.Count -gt 0) {
        Write-LifecycleEvidence 'Cleanup' ($taskErrors -join '; ') $null
        throw ('Could not safely stop leftover helpers: '+($taskErrors -join '; '))
    }
    return $stopped
}
function Stop-Cleanup($State) {
    # Keep services alive while the game is actually using them; only reap what
    # this launcher started and nothing is connected to any more.
    if ($State -and $State.Active) {
        if (Client-Running) {
            Write-Output 'Game still running; kept this profile services.'
            return
        }
        Stop-World $State
        Write-Output 'Stopped the services started by this launcher.'
    } else {
        Write-Output 'No services started by this launcher.'
    }
    $orphans = @(Stop-Orphans)
    if ($orphans.Count -gt 0) { Write-Output ("Reaped {0} leftover helper process(es)." -f $orphans.Count) }
}
function Read-LaunchRecord([string]$Path) {
    try {
        $taskRecord=[IO.File]::ReadAllText($Path) | ConvertFrom-Json -ErrorAction Stop
        if ($taskRecord -isnot [pscustomobject]) { throw 'Launch record must be a JSON object.' }
        $taskRecord | Add-Member -NotePropertyName Owned -NotePropertyValue $true -Force
        if (-not (Test-OwnedRecord $taskRecord)) { throw 'Launch record has no complete PID, path and start time.' }
        return $taskRecord
    } catch {
        $taskEvidence=Preserve-CorruptState $Path
        throw "Launch record is corrupt and was not trusted: $($_.Exception.Message). Evidence: $taskEvidence"
    }
}
function Remove-LaunchRecord([string]$Role) {
    $taskRoot = if ($taskInstallRoot) { $taskInstallRoot } else { $PSScriptRoot }
    foreach ($taskName in @($Role, $(if ($Role -eq 'Gateway') { 'rooms' } else { $Role }))) {
        $taskPath=Join-Path $taskRoot "runtime\owned-$Mode-$taskName.json"
        if (Test-Path -LiteralPath $taskPath) { [IO.File]::Delete($taskPath) }
    }
}
function Recover-LaunchRecords($State) {
    # A controller crash can happen after spawn but before Save-State. The
    # launcher writes this identity first, so Start/Stop can recover that process
    # without ever falling back to a name-only or PID-only kill.
    $taskRoot = if ($taskInstallRoot) { $taskInstallRoot } else { $PSScriptRoot }
    $taskDirectory=Join-Path $taskRoot 'runtime'
    if (-not (Test-Path -LiteralPath $taskDirectory)) { return }
    foreach ($taskFile in @(Get-ChildItem -LiteralPath $taskDirectory -Filter "owned-$Mode-*.json" -File -ErrorAction Stop)) {
        if ($taskFile.Name -notmatch "^owned-$([Regex]::Escape($Mode))-(server|gateway|rooms)\.json$") {
            throw "Unrecognized owned-process record: $($taskFile.FullName)"
        }
        $taskRole=$Matches[1]
        $taskRecord=Read-LaunchRecord $taskFile.FullName
        $taskExpected=if ($taskRole -eq 'server') { $taskExecutable } else { $(if ($State -and $State.Python) { [string]$State.Python } else { $taskPython }) }
        if (-not $taskExpected) { throw "The $taskRole launch record has no verified executable path." }
        $taskCurrent=if ($State) { if ($taskRole -eq 'rooms') { $State.Gateway } else { $State.$taskRole } } else { $null }
        $taskSameTime=$false
        if ($taskCurrent) {
            try {
                $taskSameTime=[Math]::Abs([DateTimeOffset]::Parse([string]$taskCurrent.Started).UtcDateTime.Ticks - [DateTimeOffset]::Parse([string]$taskRecord.Started).UtcDateTime.Ticks) -le 10
            } catch { $taskSameTime=$false }
        }
        if ($taskCurrent -and $taskCurrent.Id -eq $taskRecord.Id -and $taskCurrent.Path -eq $taskRecord.Path -and $taskSameTime) { continue }
        Stop-Recorded $taskRecord $taskExpected
        [IO.File]::Delete($taskFile.FullName)
    }
}
function Invoke-World {
    $taskState=Read-State
    $script:taskLastState=$taskState
    if ($Action -eq 'Status') {
        $taskCurrent=Get-ItemProperty -LiteralPath $taskRegistry
        $taskSame=$taskState -and $taskState.Active -and $taskState.Mode -eq $Mode -and $taskState.Target -eq $taskTarget
        $taskProbes=Test-Ports @(@($taskTarget,$taskHttp),@($taskTarget,$taskGateway),@('127.0.0.1',$taskAdminPort))
        $taskHttpUp=$taskProbes[0]
        $taskGateUp=$taskProbes[1]
        $taskFirewall=$false
        if ($Mode -eq 'host') { $taskFirewall=Test-FirewallRules 'OverFieldLocal-LAN','OverFieldLocal-LAN-Gateway' }
        [pscustomobject]@{
            HttpListening=$taskHttpUp; GatewayListening=$taskGateUp; ProxyListening=$taskProbes[2]
            ServerRunning=$taskSame -and (Process-Matches $taskState.Server)
            ServerRequired=$true; ProxyRunning=$taskSame -and (Process-Matches $taskState.Gateway)
            ProxyEnabled=$taskSame -and (Process-Matches $taskState.Gateway)
            ClientRunning=Client-Running; Mode=$Mode; RuntimeMode=if ($taskState -and $taskState.Active) { $taskState.Mode } else { '' }
            Target=$taskTarget; HttpPort=$taskHttp; GatewayPort=$taskGateway; ProfilePath=$taskProfile; FirewallConfigured=$taskFirewall
        } | ConvertTo-Json -Compress
        return
    }
    $taskPython=''
    if ($PythonExe) { $taskPython=(Get-Item -LiteralPath $PythonExe).FullName }
    else { $taskPythonCommand=Get-Command python.exe -ErrorAction SilentlyContinue; if ($taskPythonCommand) { $taskPython=$taskPythonCommand.Source } }
    if ($Action -eq 'Cleanup') { if (-not (Client-Running)) { Recover-LaunchRecords $taskState }; Stop-Cleanup $taskState; return }
    if ($Action -eq 'Stop') { Recover-LaunchRecords $taskState; Stop-World $taskState; Write-Output 'Profile services stopped; previous routing restored.'; return }
    if (-not $taskPython) { throw 'Python executable was not found.' }
    if ($Action -eq 'Backup') {
        $taskBackup=Python-Json @('backup','--base',$taskBase,'--mode',$Mode)
        Write-Output $taskBackup.backup; return
    }
    if (Client-Running) {
        if (-not $taskState -or -not $taskState.Active -or $taskState.Mode -ne $Mode -or $taskState.Target -ne $taskTarget -or $taskState.Uid -ne $Uid) { throw 'Close the game before changing its mode or UID.' }
    }
    Recover-LaunchRecords $taskState
    if ($taskState -and $taskState.Active -and ($taskState.Mode -ne $Mode -or $taskState.Target -ne $taskTarget)) { throw 'Stop the current world before switching modes or host addresses.' }
    if ($taskState -and $taskState.Active -and -not $taskState.Gateway) { Stop-World $taskState; $taskState=$null }
    # Migrate the old shared controller exactly once, preserving its proxy snapshot.
    $taskLegacy=Join-Path $taskInstallRoot 'runtime\state.json'
    if (Test-Path -LiteralPath $taskLegacy) {
        $taskLegacyState=Get-Content -LiteralPath $taskLegacy -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($taskLegacyState.Active) {
            $taskLegacyControl = Join-Path $taskAppRoot 'Lolo-Control.ps1'
            if (-not (Test-Path -LiteralPath $taskLegacyControl)) { $taskLegacyControl = Join-Path $taskInstallRoot 'Lolo-Control.ps1' }
            if (-not (Test-Path -LiteralPath $taskLegacyControl)) { throw 'Active legacy services detected. Stop them using the original launcher before using this clean public build. Do not copy private runtime state into a release folder.' }
            & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $taskLegacyControl -Action Stop -StopServer
            if ($LASTEXITCODE -ne 0) { throw 'Could not stop the previous shared services.' }
        }
    }
    if ($LaunchClient -and (-not $taskOriginalClient -or -not (Test-Path -LiteralPath $taskOriginalClient))) { throw 'Game client not found. Select the folder that contains OverField Game or launcher.exe.' }
    if ($taskState) {
        foreach ($taskRole in @('Proxy','Gateway','Server')) {
            if ($taskState.$taskRole -and -not (Test-OwnedRecord $taskState.$taskRole)) { throw "The $taskRole process record is incomplete; refusing to replace it." }
        }
        # Recover from a crash between process termination and the state update.
        # Never overwrite an inactive record that still identifies a live owned process.
        if (-not $taskState.Active -or $taskState.Target -ne $taskTarget) {
            Stop-World $taskState
            $taskState=$null
        }
    }
    $taskInfo=Python-Json @('prepare','--root',$GameRoot,'--base',$taskBase,'--mode',$Mode,'--host',$taskTarget)
    if (-not $taskState -or -not $taskState.Active) {
        $taskBefore=Get-ItemProperty -LiteralPath $taskRegistry
        $taskSnapshot=@{}
        foreach ($taskName in @('ProxyEnable','ProxyServer','ProxyOverride','AutoConfigURL')) {
            $taskValue=$taskBefore.PSObject.Properties[$taskName]
            $taskSnapshot[$taskName]=@{ Exists=$null -ne $taskValue; Value=if ($taskValue) { $taskValue.Value } else { $null } }
        }
        $taskState=[pscustomobject]@{ Active=$true; Mode=$Mode; Target=$taskTarget; Profile=$taskProfile; Uid=$Uid; Python=$taskPython; ProxyBefore=$null; PacUrl=''; Server=$null; Proxy=$null; Gateway=$null }
        Save-State $taskState
    }
    if (-not $taskState.Python) {
        if ($taskState.Gateway -and -not [string]::Equals([string]$taskState.Gateway.Path,$taskPython,[StringComparison]::OrdinalIgnoreCase)) {
            throw 'The existing gateway uses a different Python executable; stop it with that launcher runtime.'
        }
        $taskState.Python=$taskPython
        Save-State $taskState
    }
    try {
        if (-not (Process-Matches $taskState.Server)) {
            foreach ($taskPort in @($taskHttp,$taskGateway,($taskGateway+1),$taskLogPort)) { if (Test-Port $taskTarget $taskPort) { throw "Profile port $taskPort is occupied by another service." } }
            $taskState.Server=Start-Background $taskExecutable $taskProfile 'server' @('-c',(Join-Path $taskProfile 'config.json'))
            Save-State $taskState
            Wait-Ready $taskTarget $taskHttp $taskState.Server
            Wait-Ready '127.0.0.1' ($taskGateway+1) $taskState.Server
        }
        if (-not (Process-Matches $taskState.Gateway)) {
            if ((Test-Port $taskTarget $taskGateway) -or (Test-Port '127.0.0.1' $taskAdminPort)) { throw 'The room gateway port is occupied.' }
            $taskRoomArgs=@('-X','utf8',(Join-Path $taskAppRoot 'room_gateway.py'),'--profile',$taskProfile,'--host',$taskTarget,'--port',"$taskGateway",'--backend-port',"$($taskGateway+1)",'--admin-port',"$taskAdminPort")
            $taskState.Gateway=Start-Background $taskPython $taskInstallRoot 'rooms' $taskRoomArgs
            Save-State $taskState
            Wait-Ready $taskTarget $taskGateway $taskState.Gateway
            Wait-Ready '127.0.0.1' $taskAdminPort $taskState.Gateway
        }
        # The isolated client redirects only its own dispatch connections.
        # Existing system proxy and PAC settings are not modified.
        Restore-Route $taskState
        $taskUidRun = Invoke-PythonHelper @('-X','utf8',(Join-Path $taskAppRoot 'capture_import.py'),'uid','--profile',$taskProfile,'--uid',"$Uid")
        if ($taskUidRun.Code -ne 0) {
            $detail = Read-ErrorDetail (@($taskUidRun.Out) + @($taskUidRun.Err))
            if ($detail) { throw "UID account could not be prepared: $detail" }
            throw 'UID account could not be prepared.'
        }
        $taskUidData = ($taskUidRun.Out | Out-String) | ConvertFrom-Json
        $taskState.Uid=$Uid; Save-State $taskState
        Write-Output "World ready: $Mode $taskTarget UID $Uid."
        if ($taskInfo.imported) { Write-Output 'Previous progress imported into the isolated solo save.' }
        if ($LaunchClient) {
            if (Client-Running) { Write-Output 'Client already running; kept current session.' }
            else {
                Python-Json @('client','--root',$GameRoot,'--base',$taskBase,'--mode',$Mode) | Out-Null
                $taskSdkLog=Join-Path $taskProfile 'logs\uid-sdk.log'
                $taskClientArguments=@('-X','utf8',(Join-Path $taskAppRoot 'service_runner.py'),'--visible','--executable',$taskClient,'--cwd',(Split-Path -Parent $taskClient),
                    '--stdout',(Join-Path $taskProfile 'logs\client-stdout.log'),'--stderr',(Join-Path $taskProfile 'logs\client-stderr.log'),
                    '--env',"LOLO_SDK_UID=$($taskUidData.sdkUid)",'--env',"LOLO_SDK_LOG=$taskSdkLog",'--env',"LOLO_DISPATCH_HOST=$taskTarget",'--env',"LOLO_DISPATCH_PORT=$taskHttp",'--','-logFile',(Join-Path $taskProfile 'logs\Player.log'))
                $taskClientRun = Invoke-PythonHelper $taskClientArguments
                if ($taskClientRun.Code -ne 0) {
                    $detail = Read-ErrorDetail (@($taskClientRun.Out) + @($taskClientRun.Err)) (Join-Path $taskProfile 'logs') 'client-'
                    if ($detail) { throw "Local UID client could not launch: $detail" }
                    throw 'Local UID client could not launch.'
                }
                Write-Output 'Client launched with local UID SDK.'
            }
        }
    } catch {
        $taskStartError=$_
        try { Stop-World $taskState } catch { throw ("$($taskStartError.Exception.Message); cleanup failed: $($_.Exception.Message)") }
        throw $taskStartError
    }
}

$taskExitCode=0
$taskLock=[Threading.Mutex]::new($false,"Local\OverFieldWorldControl-$Mode")
$taskLockHeld=$false
try {
    if ($Action -ne 'Status') {
        try { $taskLockHeld=$taskLock.WaitOne(0) } catch [Threading.AbandonedMutexException] { $taskLockHeld=$true }
        if (-not $taskLockHeld) { throw 'Another world operation is running. Try again shortly.' }
    }
    $taskOutput=(Invoke-World | Out-String).Trim()
    $taskResult=@{ Code=0; Output=$taskOutput; Error='' }
} catch { $taskExitCode=1; $taskResult=@{ Code=1; Output=''; Error=$_.Exception.Message }; Write-LifecycleEvidence $Action $taskResult.Error $script:taskLastState }
finally { if ($taskLockHeld) { $taskLock.ReleaseMutex() }; $taskLock.Dispose() }
if ($ResultPath) {
    [IO.Directory]::CreateDirectory((Split-Path -Parent $ResultPath)) | Out-Null
    $taskResultTemporary="$ResultPath.$([Guid]::NewGuid().ToString('N')).tmp"
    try {
        [IO.File]::WriteAllText($taskResultTemporary,($taskResult | ConvertTo-Json -Compress -Depth 8),[Text.UTF8Encoding]::new($false))
        if (Test-Path -LiteralPath $ResultPath) {
            # Windows PowerShell 5 rejects a null backup path on Replace.
            $taskResultBackup="$ResultPath.bak"
            [IO.File]::Replace($taskResultTemporary,$ResultPath,$taskResultBackup)
            if (Test-Path -LiteralPath $taskResultBackup) { [IO.File]::Delete($taskResultBackup) }
        }
        else { [IO.File]::Move($taskResultTemporary,$ResultPath) }
    } finally { if (Test-Path -LiteralPath $taskResultTemporary) { [IO.File]::Delete($taskResultTemporary) } }
}
else { if ($taskExitCode) { Write-Error $taskResult.Error } else { Write-Output $taskResult.Output } }
exit $taskExitCode
