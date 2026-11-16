$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$release = Get-Content (Join-Path $root 'scripts\runtime-release.json') -Raw | ConvertFrom-Json
$server = Join-Path $env:USERPROFILE '.local\bin\action-server.exe'
$state = Join-Path $root '.state'
$rcc = Join-Path $env:LOCALAPPDATA "OfficeActionServer\bin\rcc-runtime-$($release.version).exe"
$seed = Join-Path $env:LOCALAPPDATA 'OfficeActionServer\bootstrap-rcc'
$bootstrapPackage = Join-Path $state 'bootstrap-package'
$taskData = Join-Path $state 'bootstrap-tasks'
$bootstrapScript = Join-Path $root 'scripts\bootstrap_runtime.py'
$packageYaml = Get-Content (Join-Path $root 'package.yaml') -Raw
$pythonPins = [regex]::Matches($packageYaml, '(?m)^\s+- python=([0-9]+\.[0-9]+\.[0-9]+)\s*$')
$pyyamlPins = [regex]::Matches($packageYaml, '(?m)^\s+- pyyaml=([0-9]+\.[0-9]+\.[0-9]+)\s*$')
if ($pythonPins.Count -ne 1 -or $pyyamlPins.Count -ne 1) { throw 'Expected exactly one pinned Python and PyYAML version in package.yaml.' }
$manifest = [ordered]@{
    'spec-version' = 'v2'
    name = 'office-bridge-bootstrap'
    description = 'Minimal isolated environment for Office Bridge RCC trust setup.'
    version = '0.1.0'
    dependencies = [ordered]@{
        'conda-forge' = @("python=$($pythonPins[0].Groups[1].Value)")
        pypi = @("pyyaml=$($pyyamlPins[0].Groups[1].Value)")
    }
    'dev-tasks' = [ordered]@{
        'bootstrap-rcc' = "python `"$bootstrapScript`""
    }
}
$appHome = if ($env:ACTIONS_HOME) { $env:ACTIONS_HOME } else { Join-Path $env:LOCALAPPDATA 'OfficeActionServer\rcc-actions' }
$oldActionsHome = $env:ACTIONS_HOME
$oldRobocorpHome = $env:ROBOCORP_HOME
$oldTargetActionsHome = $env:OFFICE_BRIDGE_TARGET_ACTIONS_HOME
$oldBootstrapRcc = $env:OFFICE_BRIDGE_BOOTSTRAP_RCC
try {
    $env:ROBOCORP_HOME = $seed
    New-Item -ItemType Directory -Force -Path $seed | Out-Null
    & icacls.exe $seed '/inheritance:r' '/grant:r' "*$([Security.Principal.WindowsIdentity]::GetCurrent().User.Value)`:(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not restrict bootstrap RCC home permissions.' }
    $profilePath = Join-Path $seed 'office-bridge-bootstrap-profile.json'
    $configPath = Join-Path $seed 'rcc.yaml'
    if ((Test-Path -LiteralPath $configPath) -and -not (Test-Path -LiteralPath $profilePath)) {
        throw 'Bootstrap RCC home contains an unexpected rcc.yaml; refusing to overwrite it.'
    }
    if (-not (Test-Path -LiteralPath $rcc)) {
        & $server download-rcc --file $rcc
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $rcc)) { throw 'Official Actions Runtime could not download its pinned RCC helper.' }
    }
    $diagnostics = & $rcc configuration diagnostics --quick --json
    if ($LASTEXITCODE -ne 0) { throw 'Could not inspect the isolated bootstrap RCC profile.' }
    $activeProfile = (($diagnostics -join "`n") | ConvertFrom-Json).details.'config-active-profile'
    if ($activeProfile -and $activeProfile -notin @('default', 'office-bridge-bootstrap')) {
        throw "Bootstrap RCC home has unexpected active profile '$activeProfile'; refusing to overwrite it."
    }

    $certs = @(Get-ChildItem Cert:\CurrentUser\Root,Cert:\LocalMachine\Root | Sort-Object Thumbprint -Unique | ForEach-Object {
        $encoded = [Convert]::ToBase64String($_.RawData)
        $lines = for ($i = 0; $i -lt $encoded.Length; $i += 64) { $encoded.Substring($i, [Math]::Min(64, $encoded.Length - $i)) }
        "-----BEGIN CERTIFICATE-----`n$($lines -join "`n")`n-----END CERTIFICATE-----"
    })
    if ($certs.Count -lt 10) { throw 'Could not read the Windows trusted root stores.' }
    $profile = @{
        name = 'office-bridge-bootstrap'
        description = 'Office Bridge bootstrap trust; Windows roots and SSL verification enabled.'
        settings = @{ meta = @{ name = 'office-bridge-bootstrap'; source = 'Office Bridge bootstrap'; version = '1' }; certificates = @{ 'verify-ssl' = $true; 'ssl-no-revoke' = $true; 'legacy-renegotiation-allowed' = $false } }
        'ca-bundle' = ($certs -join "`n") + "`n"
    }
    [IO.File]::WriteAllText($profilePath, ($profile | ConvertTo-Json -Depth 8), [Text.UTF8Encoding]::new($false))
    & $rcc configuration import --filename $profilePath --switch
    if ($LASTEXITCODE -ne 0) { throw 'Could not import the isolated bootstrap RCC trust profile.' }

    New-Item -ItemType Directory -Force -Path $bootstrapPackage | Out-Null
    $manifestPath = Join-Path $bootstrapPackage 'package.yaml'
    [IO.File]::WriteAllText($manifestPath, ($manifest | ConvertTo-Json -Depth 8), [Text.UTF8Encoding]::new($false))
    $env:ACTIONS_HOME = $seed
    $env:OFFICE_BRIDGE_TARGET_ACTIONS_HOME = $appHome
    $env:OFFICE_BRIDGE_BOOTSTRAP_RCC = $rcc
    Push-Location $bootstrapPackage
    try { & $server devenv --datadir $taskData task bootstrap-rcc }
    finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { throw 'RCC managed bootstrap task failed.' }
} finally {
    if ($null -eq $oldActionsHome) { Remove-Item Env:ACTIONS_HOME -ErrorAction SilentlyContinue } else { $env:ACTIONS_HOME = $oldActionsHome }
    if ($null -eq $oldRobocorpHome) { Remove-Item Env:ROBOCORP_HOME -ErrorAction SilentlyContinue } else { $env:ROBOCORP_HOME = $oldRobocorpHome }
    if ($null -eq $oldTargetActionsHome) { Remove-Item Env:OFFICE_BRIDGE_TARGET_ACTIONS_HOME -ErrorAction SilentlyContinue } else { $env:OFFICE_BRIDGE_TARGET_ACTIONS_HOME = $oldTargetActionsHome }
    if ($null -eq $oldBootstrapRcc) { Remove-Item Env:OFFICE_BRIDGE_BOOTSTRAP_RCC -ErrorAction SilentlyContinue } else { $env:OFFICE_BRIDGE_BOOTSTRAP_RCC = $oldBootstrapRcc }
}
