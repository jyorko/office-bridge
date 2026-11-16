function New-TeamsCodeSnapshot {
    param(
        [Parameter(Mandatory=$true)][string]$Source,
        [Parameter(Mandatory=$true)][string]$Generation
    )
    if ($Generation -notmatch '^[0-9a-f]{32}$') { throw 'Invalid Teams host generation.' }
    $snapshotRoot = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'OfficeActionServer\teams-host-code'))
    $snapshotPath = [IO.Path]::GetFullPath((Join-Path $snapshotRoot $Generation))
    if (-not $snapshotPath.StartsWith($snapshotRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Teams code snapshot escaped its private directory.'
    }
    if (Test-Path -LiteralPath $snapshotPath) { throw 'Teams code snapshot generation already exists.' }
    New-Item -ItemType Directory -Path $snapshotPath -Force | Out-Null
    foreach ($fileName in @('TeamsSession.cs', 'chat.js', 'formatting.js', 'lookup.js', 'copilot.js')) {
        $sourceFile = Join-Path $Source $fileName
        $snapshotFile = Join-Path $snapshotPath $fileName
        $before = (Get-FileHash -LiteralPath $sourceFile -Algorithm SHA256).Hash
        Copy-Item -LiteralPath $sourceFile -Destination $snapshotFile
        if ($before -ne (Get-FileHash -LiteralPath $snapshotFile -Algorithm SHA256).Hash -or
            $before -ne (Get-FileHash -LiteralPath $sourceFile -Algorithm SHA256).Hash) {
            throw 'Teams source changed during snapshot creation; host startup stopped.'
        }
    }
    return $snapshotPath
}
