$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$state = Join-Path $taskRoot '.state'
$state = [IO.Path]::GetFullPath($state)
New-Item -ItemType Directory -Force -Path $state | Out-Null
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
& icacls.exe $state '/inheritance:r' '/grant:r' "*$identity`:(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not restrict .state permissions.' }

$release = Get-Content (Join-Path $taskRoot 'scripts\runtime-release.json') -Raw | ConvertFrom-Json
$target = Join-Path $env:USERPROFILE '.local\bin\action-server.exe'
$targetDir = Split-Path -Parent $target
New-Item -ItemType Directory -Force -Path $targetDir | Out-Null

function Get-Sha256([string]$Path) { (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }
if (-not (Test-Path -LiteralPath $target) -or (Get-Sha256 $target) -ne $release.asset_sha256) {
    $stage = Join-Path $targetDir ('.download-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $stage | Out-Null
    try {
        $manifest = Join-Path $stage 'checksums.txt'
        $binary = Join-Path $stage $release.asset
        Invoke-WebRequest -UseBasicParsing -Uri "$($release.release_url)/actions-runtime-$($release.version)-sha256.txt" -OutFile $manifest
        if ((Get-Sha256 $manifest) -ne $release.checksum_sha256) { throw 'Official Actions Runtime checksum manifest hash mismatch.' }
        $manifestLine = Select-String -LiteralPath $manifest -Pattern ('^([0-9a-f]{64})\s+' + [regex]::Escape($release.asset) + '\s*$')
        if (-not $manifestLine -or $manifestLine.Matches[0].Groups[1].Value -ne $release.asset_sha256) { throw 'Official manifest does not contain the pinned executable hash.' }
        Invoke-WebRequest -UseBasicParsing -Uri "$($release.release_url)/$($release.asset)" -OutFile $binary
        if ((Get-Sha256 $binary) -ne $release.asset_sha256) { throw 'Actions Runtime executable hash mismatch.' }
        if (Test-Path -LiteralPath $target) {
            $backup = Join-Path $state "action-server.exe.before-runtime-$($release.version)"
            if (-not (Test-Path -LiteralPath $backup)) { Copy-Item -LiteralPath $target -Destination $backup }
        }
        Move-Item -LiteralPath $binary -Destination $target -Force
    } finally {
        $canonicalParent = [IO.Path]::GetFullPath($targetDir).TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
        $canonicalStage = [IO.Path]::GetFullPath($stage)
        $stageLeaf = [IO.Path]::GetFileName($canonicalStage)
        $parentPrefix = $canonicalParent + [IO.Path]::DirectorySeparatorChar
        if ($stageLeaf -notmatch '^\.download-[0-9a-f]{32}$' -or
            -not $canonicalStage.StartsWith($parentPrefix, [StringComparison]::OrdinalIgnoreCase) -or
            [IO.Path]::GetDirectoryName($canonicalStage) -ne $canonicalParent) {
            throw "Refusing to recursively remove unexpected staging path: $canonicalStage"
        }
        if (Test-Path -LiteralPath $canonicalStage) {
            Remove-Item -LiteralPath $canonicalStage -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}
Write-Output "Pinned Actions Runtime $($release.version) executable is ready."
& (Join-Path $PSScriptRoot 'initialize-rcc.ps1')
if ($LASTEXITCODE -ne 0) { throw 'Managed RCC bootstrap initialization failed.' }
