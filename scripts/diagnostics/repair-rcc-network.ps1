$ErrorActionPreference = 'Stop'
$rccExe = Join-Path $env:USERPROFILE '.local\bin\rcc.exe'
$stateDir = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\.state'))
$backupDir = Join-Path $stateDir 'laptop-rcc-backup'
New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
$rccHomes = @((Join-Path $env:LOCALAPPDATA 'robocorp'), (Join-Path $env:LOCALAPPDATA 'actions'))
foreach ($rccHome in $rccHomes) {
    $label = Split-Path -Leaf $rccHome
    foreach ($name in @('rcc.yaml','settings.yaml','ca-bundle.pem','micromambarc','pip.ini')) {
        $source = Join-Path $rccHome $name
        $backup = Join-Path $backupDir ($label + '-' + $name)
        if ((Test-Path -LiteralPath $source) -and -not (Test-Path -LiteralPath $backup)) { Copy-Item -LiteralPath $source -Destination $backup }
    }
}
$trustedRoots = @(Get-ChildItem Cert:\CurrentUser\Root,Cert:\LocalMachine\Root | Sort-Object Thumbprint -Unique)
if ($trustedRoots.Count -lt 10) { throw 'Could not read the Windows trusted root stores.' }
$pem = ($trustedRoots | ForEach-Object {
    "-----BEGIN CERTIFICATE-----`n" + [Convert]::ToBase64String($_.RawData,[Base64FormattingOptions]::InsertLineBreaks) + "`n-----END CERTIFICATE-----`n"
}) -join "`n"
$profile = @{
    name = 'gainwell-company-ca'
    description = 'Windows trusted roots including the Gainwell Netskope CA; certificate verification enabled. Netskope certificates omit revocation endpoints.'
    settings = @{
        certificates = @{ 'verify-ssl' = $true; 'ssl-no-revoke' = $true; 'legacy-renegotiation-allowed' = $false }
        meta = @{ name = 'gainwell-company-ca'; description = 'Windows trusted roots for the Gainwell network'; source = 'Windows certificate stores'; version = '1' }
    }
    'ca-bundle' = $pem
}
$profileFile = Join-Path $stateDir 'gainwell-company-ca.yaml'
[IO.File]::WriteAllText($profileFile, (ConvertTo-Json -Depth 5 -InputObject $profile), (New-Object Text.UTF8Encoding $false))
$previousRccHome = $env:ROBOCORP_HOME
try {
    foreach ($rccHome in $rccHomes) {
        $env:ROBOCORP_HOME = $rccHome
        & $rccExe configuration import --filename $profileFile --switch
        if ($LASTEXITCODE -ne 0) { throw ('Profile import failed for ' + $rccHome) }
        $ErrorActionPreference = 'Continue'
        $raw = & $rccExe diagnostics --quick --json 2>$null
        $diagnosticExit = $LASTEXITCODE
        $ErrorActionPreference = 'Stop'
        if ($diagnosticExit -ne 0) { throw 'RCC diagnostics failed.' }
        $diagnostics = $raw | ConvertFrom-Json
        if ($diagnostics.details.'config-ssl-verify' -ne 'true' -or $diagnostics.details.'config-active-profile' -ne 'gainwell-company-ca') { throw ('Profile validation failed for ' + $rccHome) }
        Write-Output ((Split-Path -Leaf $rccHome) + ': company CA profile active; SSL verification enabled.')
    }
} finally {
    $env:ROBOCORP_HOME = $previousRccHome
}
$curlConfig = Join-Path $env:USERPROFILE '.curlrc'
if (Test-Path -LiteralPath $curlConfig) {
    $curlBytes = [IO.File]::ReadAllBytes($curlConfig)
    if ($curlBytes.Length -ge 3 -and $curlBytes[0] -eq 239 -and $curlBytes[1] -eq 187 -and $curlBytes[2] -eq 191) {
        $curlBackup = Join-Path $backupDir 'curlrc.before'
        if (-not (Test-Path -LiteralPath $curlBackup)) { Copy-Item -LiteralPath $curlConfig -Destination $curlBackup }
        $curlText = [IO.File]::ReadAllText($curlConfig)
        [IO.File]::WriteAllText($curlConfig,$curlText,(New-Object Text.UTF8Encoding $false))
        Write-Output 'Removed the invalid UTF-8 byte-order mark from .curlrc; existing options preserved.'
    }
}
Write-Output ('RCC profiles contain ' + $trustedRoots.Count + ' unique Windows trusted roots. Backups: ' + $backupDir)
