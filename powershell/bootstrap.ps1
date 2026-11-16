param(
    [switch]$Dev,
    [switch]$SkipCodex,
    [switch]$PrepareOnly,
    [string]$TeamsAccount
)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskData = [IO.Path]::GetFullPath((Join-Path $taskRoot '.state\tasks'))
$stateDir = Join-Path $taskRoot '.state'
$actionServer = Join-Path $env:USERPROFILE '.local\bin\action-server.exe'

if (-not $PrepareOnly -and [string]::IsNullOrWhiteSpace($TeamsAccount) -and
    [string]::IsNullOrWhiteSpace($env:OFFICE_BRIDGE_TEAMS_ACCOUNT) -and
    -not (Test-Path -LiteralPath (Join-Path $env:LOCALAPPDATA 'OfficeActionServer\config\settings.json'))) {
    $detectedAccount = ''
    try {
        $detectedAccount = (& (Join-Path $env:SystemRoot 'System32\whoami.exe') /upn 2>$null | Out-String).Trim()
        if ($LASTEXITCODE -ne 0) { $detectedAccount = '' }
    } catch { $detectedAccount = '' }
    if ($detectedAccount -notmatch '^[^@\s]+@[^@\s]+\.[^@\s]+$') {
        throw 'Windows could not identify your work account. Run bootstrap.ps1 -TeamsAccount your.name@company.com.'
    }
    $TeamsAccount = $detectedAccount
    Write-Output 'Using the Windows work account for Teams identity verification.'
}

& (Join-Path $PSScriptRoot 'setup.ps1')
if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { throw 'Managed Python setup failed.' }
if (-not (Test-Path -LiteralPath $actionServer)) { throw 'The official action-server.exe is missing after setup.' }
New-Item -ItemType Directory -Path $stateDir -Force | Out-Null

$oldActionsHome = $env:ACTIONS_HOME
$hadActionsHome = Test-Path Env:ACTIONS_HOME
$oldTeamsAccount = $env:OFFICE_BRIDGE_TEAMS_ACCOUNT
$hadTeamsAccount = Test-Path Env:OFFICE_BRIDGE_TEAMS_ACCOUNT
try {
    if ([string]::IsNullOrWhiteSpace($env:ACTIONS_HOME)) { $env:ACTIONS_HOME = Join-Path $env:LOCALAPPDATA 'OfficeActionServer\rcc-actions' }
    if (-not [string]::IsNullOrWhiteSpace($TeamsAccount)) { $env:OFFICE_BRIDGE_TEAMS_ACCOUNT = $TeamsAccount }

    # Install the pinned SDK, then use the SDK to check the Evergreen Runtime.
    & $actionServer devenv --datadir $taskData task install-webview2
    if ($LASTEXITCODE -ne 0) { throw 'WebView2 SDK download or integrity verification failed.' }
    $sdkRoot = Join-Path $env:LOCALAPPDATA 'OfficeActionServer\webview2-sdk\1.0.4314.35'
    $coreDll = Join-Path $sdkRoot 'Microsoft.Web.WebView2.Core.dll'
    Add-Type -Path $coreDll
    $browserVersion = $null
    try { $browserVersion = [Microsoft.Web.WebView2.Core.CoreWebView2Environment]::GetAvailableBrowserVersionString() } catch { }
    if (-not $browserVersion) {
        $installer = Join-Path $stateDir 'MicrosoftEdgeWebView2Setup.exe'
        Invoke-WebRequest -Uri 'https://go.microsoft.com/fwlink/p/?LinkId=2124703' -OutFile $installer
        $signature = Get-AuthenticodeSignature -LiteralPath $installer
        if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Microsoft Corporation') {
            Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue
            throw 'The WebView2 Evergreen installer does not have a valid Microsoft signature.'
        }
        $install = Start-Process -FilePath $installer -ArgumentList '/silent','/install' -PassThru -Wait -WindowStyle Hidden
        Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue
        if ($install.ExitCode -ne 0) { throw "WebView2 Runtime installation failed with exit code $($install.ExitCode)." }
        try { $browserVersion = [Microsoft.Web.WebView2.Core.CoreWebView2Environment]::GetAvailableBrowserVersionString() } catch { }
        if (-not $browserVersion) { throw 'WebView2 installer completed, but the Runtime is still unavailable.' }
    }
    Write-Output "WebView2 Runtime available: $browserVersion"

    if ($PrepareOnly) {
        Write-Output 'Bootstrap prerequisites are prepared. Run bootstrap.ps1 again without -PrepareOnly to verify and start the service.'
        return
    }

    $startScript = Join-Path $PSScriptRoot 'start-office.ps1'
    $startArgs = @{ Dev = [bool]$Dev }
    if (-not [string]::IsNullOrWhiteSpace($TeamsAccount)) { $startArgs.TeamsAccount = $TeamsAccount }
    & $startScript @startArgs
    Remove-Item Env:OFFICE_BRIDGE_TEAMS_ACCOUNT -ErrorAction SilentlyContinue

    $verifyTask = if ($Dev) { 'verify-dev' } else { 'verify' }
    & $actionServer devenv --datadir $taskData task $verifyTask
    if ($LASTEXITCODE -ne 0) { throw 'Bootstrap verification failed.' }
    if (-not $SkipCodex) {
        $registerTask = if ($Dev) { 'register-dev' } else { 'register' }
        & $actionServer devenv --datadir $taskData task $registerTask
        if ($LASTEXITCODE -ne 0) { throw 'Codex MCP registration failed.' }
    }
} finally {
    if ($hadActionsHome) { $env:ACTIONS_HOME = $oldActionsHome } else { Remove-Item Env:ACTIONS_HOME -ErrorAction SilentlyContinue }
    if ($hadTeamsAccount) { $env:OFFICE_BRIDGE_TEAMS_ACCOUNT = $oldTeamsAccount } else { Remove-Item Env:OFFICE_BRIDGE_TEAMS_ACCOUNT -ErrorAction SilentlyContinue }
}
