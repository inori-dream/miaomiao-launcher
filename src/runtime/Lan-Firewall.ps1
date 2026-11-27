[CmdletBinding()]
param([string]$GameRoot='',[Parameter(Mandatory=$true)][string]$HostAddress,[string]$ServerExe,[string]$PythonExe)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$taskInstallRoot=if ((Split-Path -Leaf $PSScriptRoot) -ieq 'app') { Split-Path -Parent $PSScriptRoot } else { $PSScriptRoot }
$taskRuntime=Join-Path $taskInstallRoot 'runtime'
$taskResultPath=Join-Path $taskRuntime 'lan-firewall-result.json'
$taskRuleNames=@('OverFieldLocal-LAN','OverFieldLocal-LAN-Gateway')
$taskDisplayNames=@('OverField host mode world','OverField host mode UID room gateway')
$taskCreated=@()
function Write-FirewallResult([bool]$Configured,[string]$Error='') {
    [IO.Directory]::CreateDirectory($taskRuntime) | Out-Null
    $taskTemporary="$taskResultPath.$([Guid]::NewGuid().ToString('N')).tmp"
    $taskValue=@{ configured=$Configured; mode='host'; host=$HostAddress; error=$Error; created=[DateTime]::UtcNow.ToString('o') } | ConvertTo-Json -Compress
    try {
        [IO.File]::WriteAllText($taskTemporary,$taskValue,[Text.UTF8Encoding]::new($false))
        if (Test-Path -LiteralPath $taskResultPath) {
            $taskBackup="$taskResultPath.bak"
            [IO.File]::Replace($taskTemporary,$taskResultPath,$taskBackup)
            if (Test-Path -LiteralPath $taskBackup) { [IO.File]::Delete($taskBackup) }
        }
        else { [IO.File]::Move($taskTemporary,$taskResultPath) }
    } finally { if (Test-Path -LiteralPath $taskTemporary) { [IO.File]::Delete($taskTemporary) } }
}
function Remove-CreatedRules {
    foreach ($taskName in $taskCreated) {
        try { Get-NetFirewallRule -Name $taskName -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction Stop } catch { }
    }
}
try {
    $taskAddress=[Net.IPAddress]::Parse($HostAddress)
    $taskBytes=$taskAddress.GetAddressBytes()
    if ($taskBytes.Length -ne 4 -or -not ($taskBytes[0] -eq 10 -or ($taskBytes[0] -eq 172 -and $taskBytes[1] -ge 16 -and $taskBytes[1] -le 31) -or ($taskBytes[0] -eq 192 -and $taskBytes[1] -eq 168))) {
        throw 'A LAN IPv4 address is required.'
    }
    $taskLocal=@([Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces() | ForEach-Object { $_.GetIPProperties().UnicastAddresses } | ForEach-Object { $_.Address.ToString() })
    if ($HostAddress -notin $taskLocal) { throw 'Host address does not belong to this computer.' }
    # This script intentionally configures only the host edition. Solo and Steam
    # use loopback and different ports, and never share these inbound rules.
    $taskExpectedServer=Join-Path $taskInstallRoot 'profiles\host\server\Lolo.exe'
    $taskServer=(Get-Item -LiteralPath $(if ($ServerExe) { $ServerExe } else { $taskExpectedServer })).FullName
    if (-not [string]::Equals($taskServer,$taskExpectedServer,[StringComparison]::OrdinalIgnoreCase)) {
        throw 'Firewall rules may only target the isolated host-mode server.'
    }
    $taskPython=(Get-Item -LiteralPath $PythonExe).FullName
    $taskIdentity=[Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $taskIdentity.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Administrator permission is required for this LAN firewall rule.' }
    try {
        $taskExisting=@(Get-NetFirewallRule -Name $taskRuleNames -ErrorAction SilentlyContinue)
        foreach ($taskRule in $taskExisting) {
            $taskIndex=[Array]::IndexOf($taskRuleNames,[string]$taskRule.Name)
            if ($taskIndex -lt 0 -or [string]$taskRule.DisplayName -ne $taskDisplayNames[$taskIndex]) {
                throw "Firewall rule name $($taskRule.Name) belongs to another configuration; it was not removed."
            }
        }
        if ($taskExisting.Count -gt 0) { $taskExisting | Remove-NetFirewallRule -ErrorAction Stop }
        New-NetFirewallRule -Name 'OverFieldLocal-LAN' -DisplayName 'OverField host mode world' -Description 'Inbound allow for profiles/host only; no effect on solo or Steam modes.' -Direction Inbound -Action Allow -Protocol TCP `
            -LocalPort 19080,19200 -LocalAddress $HostAddress -RemoteAddress LocalSubnet -Program $taskServer -Profile Any | Out-Null
        $taskCreated+='OverFieldLocal-LAN'
        New-NetFirewallRule -Name 'OverFieldLocal-LAN-Gateway' -DisplayName 'OverField host mode UID room gateway' -Description 'Inbound allow for the host-mode gateway port only.' -Direction Inbound -Action Allow -Protocol TCP `
            -LocalPort 19100 -LocalAddress $HostAddress -RemoteAddress LocalSubnet -Program $taskPython -Profile Any | Out-Null
        $taskCreated+='OverFieldLocal-LAN-Gateway'
        $taskVerify=@(Get-NetFirewallRule -Name $taskRuleNames -ErrorAction Stop)
        if ($taskVerify.Count -ne 2 -or @($taskVerify | Where-Object { -not $_.Enabled }).Count -ne 0) { throw 'LAN firewall rules could not be verified.' }
    } catch {
        Remove-CreatedRules
        throw
    }
    Write-FirewallResult $true ''
} catch {
    Remove-CreatedRules
    $taskMessage=$_.Exception.Message
    try { Write-FirewallResult $false $taskMessage } catch { }
    Write-Error $taskMessage
    exit 1
}
