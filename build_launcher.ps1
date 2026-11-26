param(
    [ValidateSet('Both','CN','EN')][string]$Edition='Both',
    [string]$OutputDirectory=$PSScriptRoot,
    [string]$IntermediateDirectory='',
    [string]$SourceDirectory=$PSScriptRoot
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# Build only from a private snapshot so another editor cannot change one source
# file halfway through a two-edition build. Defaults retain the original layout.
$taskRoot = [IO.Path]::GetFullPath($SourceDirectory)
$taskOutput = [IO.Path]::GetFullPath($OutputDirectory)
$taskKeepIntermediate = $PSBoundParameters.ContainsKey('IntermediateDirectory')
if (-not $IntermediateDirectory) {
    $IntermediateDirectory = Join-Path ([IO.Path]::GetTempPath()) ("miaomiao-launcher\{0}\launcher" -f [Guid]::NewGuid().ToString('N'))
}
$taskIntermediate = [IO.Path]::GetFullPath($IntermediateDirectory)
$taskSnapshot = Join-Path $taskIntermediate 'launcher-src'
$taskCompiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$taskSources = @('Launcher.cs', 'SeasideUI.cs', 'RoomManagerForm.cs', 'Strings.cs')
$taskAssets = @('launcher.manifest', 'launcher.ico')
$taskReferences = @(
    '/reference:System.Windows.Forms.dll'
    '/reference:System.Drawing.dll'
    '/reference:System.Web.Extensions.dll'
)

function Assert-Input([string]$Path) {
    if (-not [IO.File]::Exists($Path)) { throw "Required build input is missing: $Path" }
}

function ConvertTo-RvaOffset([byte[]]$Bytes, [int]$Rva, [object[]]$Sections) {
    foreach ($section in $Sections) {
        $span = [Math]::Max($section.RawSize, $section.VirtualSize)
        if ($Rva -ge $section.VirtualAddress -and $Rva -lt ($section.VirtualAddress + $span)) {
            return $section.RawPointer + ($Rva - $section.VirtualAddress)
        }
    }
    throw "Invalid PE RVA: $Rva"
}

function Save-DeterministicAssembly([string]$Path) {
    # Framework csc 14.x has no /deterministic switch. Clear the two varying PE
    # fields and derive the CLR module MVID from the normalized image itself.
    # The CLI metadata layout and #GUID stream are validated before any write.
    $bytes = [IO.File]::ReadAllBytes($Path)
    if ($bytes.Length -lt 64 -or $bytes[0] -ne 0x4D -or $bytes[1] -ne 0x5A) { throw "Compiler did not produce a PE image: $Path" }
    $stream = [IO.MemoryStream]::new($bytes, $true)
    $reader = [IO.BinaryReader]::new($stream)
    try {
        $stream.Position = 0x3C
        $pe = $reader.ReadInt32()
        if ($pe -lt 0 -or ($pe + 24) -ge $bytes.Length) { throw "Invalid PE header: $Path" }
        $stream.Position = $pe
        if ($reader.ReadUInt32() -ne 0x00004550) { throw "Invalid PE header: $Path" }
        $stream.Position = $pe + 6
        $sectionCount = $reader.ReadUInt16()
        $stream.Position = $pe + 20
        $optionalSize = $reader.ReadUInt16()
        $optional = $pe + 24
        $stream.Position = $optional
        $magic = $reader.ReadUInt16()
        if ($magic -ne 0x10B -and $magic -ne 0x20B) { throw "Unsupported PE optional header: $Path" }
        [Array]::Clear($bytes, $pe + 8, 4) # COFF TimeDateStamp
        [Array]::Clear($bytes, $optional + 64, 4) # Optional-header CheckSum
        $directory = $optional + $(if ($magic -eq 0x10B) { 96 } else { 112 })
        $stream.Position = $directory + (14 * 8)
        $cliRva = $reader.ReadInt32()
        $cliSize = $reader.ReadInt32()
        if ($cliRva -eq 0 -or $cliSize -lt 72) { throw "Managed PE has no CLI header: $Path" }
        $sections = @()
        $stream.Position = $optional + $optionalSize
        for ($i = 0; $i -lt $sectionCount; $i++) {
            $base = $optional + $optionalSize + ($i * 40)
            $stream.Position = $base + 8
            $virtualSize = $reader.ReadInt32()
            $stream.Position = $base + 12
            $virtualAddress = $reader.ReadInt32()
            $stream.Position = $base + 16
            $rawSize = $reader.ReadInt32()
            $stream.Position = $base + 20
            $rawPointer = $reader.ReadInt32()
            $sections += [pscustomobject]@{ VirtualSize=$virtualSize; VirtualAddress=$virtualAddress; RawSize=$rawSize; RawPointer=$rawPointer }
        }
        $cli = ConvertTo-RvaOffset $bytes $cliRva $sections
        $stream.Position = $cli + 8
        $metadataRva = $reader.ReadInt32()
        $metadata = ConvertTo-RvaOffset $bytes $metadataRva $sections
        $stream.Position = $metadata
        if ($reader.ReadUInt32() -ne 0x424A5342) { throw "Invalid CLI metadata signature: $Path" }
        $stream.Position = $metadata + 12
        $versionLength = $reader.ReadInt32()
        if ($versionLength -le 0 -or $versionLength -gt 1024) { throw "Invalid CLI metadata version: $Path" }
        $stream.Position = $metadata + 16 + $versionLength
        $stream.Position = [Math]::Min($bytes.Length, (($stream.Position + 3) -band -4))
        $stream.Position += 2 # Flags
        $streamCount = $reader.ReadUInt16()
        $guidOffset = -1
        $guidSize = 0
        for ($i = 0; $i -lt $streamCount; $i++) {
            $streamPosition = $stream.Position
            $offset = $reader.ReadInt32()
            $size = $reader.ReadInt32()
            $namePosition = $stream.Position
            $name = [Text.Encoding]::ASCII.GetString($bytes, $namePosition, [Math]::Min(32, $bytes.Length - $namePosition))
            $nul = $name.IndexOf([char]0)
            if ($nul -lt 0) { throw "Invalid metadata stream name: $Path" }
            $name = $name.Substring(0, $nul)
            if ($name -eq '#GUID') { $guidOffset = $metadata + $offset; $guidSize = $size }
            $stream.Position = (($namePosition + $nul + 1 + 3) -band -4)
        }
        if ($guidOffset -lt 0 -or $guidSize -lt 16 -or ($guidOffset + $guidSize) -gt $bytes.Length) { throw "CLR module MVID is missing: $Path" }
        # Framework csc also derives the compiler-generated
        # <PrivateImplementationDetails>{GUID} type name from a random GUID.
        # Normalize it before hashing, otherwise each build has a different MVID.
        $latin1 = [Text.Encoding]::GetEncoding(28591)
        $text = $latin1.GetString($bytes)
        $pattern = '<PrivateImplementationDetails>\{[0-9A-Fa-f-]{36}\}'
        foreach ($match in [regex]::Matches($text, $pattern)) {
            $start = $match.Index + $match.Value.IndexOf('{') + 1
            for ($i = 0; $i -lt 36; $i++) {
                if ($bytes[$start + $i] -ne 0x2D) { $bytes[$start + $i] = 0x30 }
            }
        }
        [Array]::Clear($bytes, $guidOffset, 16)
        $sha = [Security.Cryptography.SHA256]::Create()
        try { $digest = $sha.ComputeHash($bytes) } finally { $sha.Dispose() }
        [Array]::Copy($digest, 0, $bytes, $guidOffset, 16)
        [IO.File]::WriteAllBytes($Path, $bytes)
    } finally {
        $reader.Dispose()
        $stream.Dispose()
    }
}

function Build-Edition([string]$Name, [string[]]$Defines, [string]$Output) {
    $temporary = Join-Path $taskIntermediate ([IO.Path]::GetFileName($Output))
    $arguments = @('/nologo', '/target:winexe', '/optimize+', '/debug-', '/platform:anycpu', '/codepage:65001', '/warnaserror+') + $taskReferences
    foreach ($define in $Defines) { $arguments += "/define:$define" }
    $arguments += "/win32manifest:$taskSnapshot\launcher.manifest"
    $arguments += "/win32icon:$taskSnapshot\launcher.ico"
    $arguments += "/out:$temporary"
    foreach ($source in $taskSources) { $arguments += "$taskSnapshot\$source" }
    & $taskCompiler @arguments
    if ($LASTEXITCODE -ne 0) {
        if (Test-Path -LiteralPath $Output) { Remove-Item -LiteralPath $Output -Force }
        throw "Compilation failed for $Name; no previous binary was retained as a successful build."
    }
    try {
        Save-DeterministicAssembly $temporary
        [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($Output)) | Out-Null
        [IO.File]::Copy($temporary, $Output, $true)
    } catch {
        if (Test-Path -LiteralPath $Output) { Remove-Item -LiteralPath $Output -Force }
        throw
    }
    Write-Host ("{0} -> {1}" -f $Name, (Split-Path -Leaf $Output))
}

Assert-Input $taskCompiler
[IO.Directory]::CreateDirectory($taskSnapshot) | Out-Null
[IO.Directory]::CreateDirectory($taskOutput) | Out-Null
foreach ($name in ($taskSources + $taskAssets)) {
    $source = Join-Path $taskRoot $name
    Assert-Input $source
    [IO.File]::Copy($source, (Join-Path $taskSnapshot $name), $true)
}
$inputs = [ordered]@{}
foreach ($name in ($taskSources + $taskAssets)) {
    $hash = [Security.Cryptography.SHA256]::Create()
    try {
        $stream = [IO.File]::OpenRead((Join-Path $taskSnapshot $name))
        try { $inputs[$name] = [BitConverter]::ToString($hash.ComputeHash($stream)).Replace('-', '').ToLowerInvariant() }
        finally { $stream.Dispose() }
    } finally { $hash.Dispose() }
}
[IO.File]::WriteAllText((Join-Path $taskIntermediate 'launcher-inputs.json'), ($inputs | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
try {
    if ($Edition -in @('Both','CN')) { Build-Edition 'national (zh-CN)' @() (Join-Path $taskOutput 'OverFieldLauncher.exe') }
    if ($Edition -in @('Both','EN')) { Build-Edition 'international (en)' @('ENGLISH') (Join-Path $taskOutput 'OverFieldLauncher-EN.exe') }
} finally {
    if (-not $taskKeepIntermediate -and (Test-Path -LiteralPath $taskIntermediate)) { Remove-Item -LiteralPath $taskIntermediate -Recurse -Force }
}
