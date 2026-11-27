param([Parameter(Mandatory=$true)][string]$Destination,[Parameter(Mandatory=$true)][string]$Source)
$ErrorActionPreference='Stop'
New-Item -ItemType Junction -Path $Destination -Target $Source | Out-Null
