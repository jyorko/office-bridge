param(
    [switch]$Dev,
    [switch]$SkipCodex,
    [switch]$PrepareOnly
)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot

& (Join-Path $PSScriptRoot 'setup.ps1')
if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { throw 'Python setup failed.' }

$runtimePython = Join-Path $taskRoot '.runtime\Scripts\python.exe'
$oldPythonPath = $env:PYTHONPATH
$oldActionsHome = $env:ACTIONS_HOME
try {
    $env:PYTHONPATH = Join-Path $taskRoot 'src'
    if (-not $env:ACTIONS_HOME) {
        $env:ACTIONS_HOME = Join-Path $env:LOCALAPPDATA 'OfficeActionServer\rcc-actions'
    }
    & $runtimePython (Join-Path $taskRoot 'scripts\bootstrap_runtime.py')
    if ($LASTEXITCODE -ne 0) { throw 'RCC bootstrap failed.' }

    # Install the pinned SDK, then ask the SDK itself whether the Evergreen Runtime is present.
    & $runtimePython -m office_bridge.runtime.sdk
    if ($LASTEXITCODE -ne 0) { throw 'WebView2 SDK download or integrity verification failed.' }
    $sdkRoot = Join-Path $env:LOCALAPPDATA 'OfficeActionServer\webview2-sdk\1.0.4314.35'
    $coreDll = Join-Path $sdkRoot 'Microsoft.Web.WebView2.Core.dll'
    Add-Type -Path $coreDll
    $browserVersion = $null
    try { $browserVersion = [Microsoft.Web.WebView2.Core.CoreWebView2Environment]::GetAvailableBrowserVersionString() } catch { }
    if (-not $browserVersion) {
        $installer = Join-Path $taskRoot '.state\MicrosoftEdgeWebView2Setup.exe'
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
    & $startScript @startArgs
    if ($LASTEXITCODE -ne 0) { throw 'Office Action Server startup failed.' }

    $check = Join-Path $taskRoot 'scripts\bootstrap_check.py'
    if (-not (Test-Path -LiteralPath $check)) { throw 'scripts/bootstrap_check.py is missing.' }
    $checkArgs = @()
    if ($Dev) { $checkArgs += '--dev' }
    & $runtimePython $check @checkArgs
    if ($LASTEXITCODE -ne 0) { throw 'Bootstrap verification failed.' }
    if (-not $SkipCodex) {
        & $runtimePython (Join-Path $taskRoot 'scripts\register_mcp.py') @checkArgs
        if ($LASTEXITCODE -ne 0) { throw 'Codex MCP registration failed.' }
    }
} finally {
    $env:PYTHONPATH = $oldPythonPath
    $env:ACTIONS_HOME = $oldActionsHome
}
