param([switch]$Dev)
$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
$stateDir = Join-Path $taskRoot '.state'
$runtimePython = Join-Path $taskRoot '.runtime\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $runtimePython)) { throw 'The pinned runtime environment is missing.' }
New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
$userSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
& icacls.exe $stateDir /inheritance:r /grant:r "*${userSid}:(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not secure the local runtime data directory.' }
$pidFile = Join-Path $stateDir 'server.pid'
$activeListener = Get-NetTCPConnection -State Listen -LocalPort 8766 -ErrorAction SilentlyContinue
if ($activeListener) {
    $recorded = if (Test-Path -LiteralPath $pidFile) { Get-CimInstance Win32_Process -Filter ('ProcessId = ' + [int](Get-Content -LiteralPath $pidFile)) }
    if ($recorded -and $recorded.CommandLine.Contains((Join-Path $taskRoot 'run_server.py'))) {
        Write-Output 'Office Action Server is already listening on http://127.0.0.1:8766/mcp'
        exit 0
    }
    throw 'Port 8766 belongs to another process.'
}
$packageDir = Join-Path $taskRoot '.package'
New-Item -ItemType Directory -Path (Join-Path $packageDir 'powershell') -Force | Out-Null
foreach ($file in @('package.yaml', 'office_actions.py', 'attachment_actions.py', 'teams_web_actions.py', 'teams_web_backend.py', 'teams_web_session.py', 'teams_send_ledger.py', 'desktop_bridge.py', 'install_webview2_sdk.py', 'powershell\office.ps1', 'powershell\attachments.ps1', 'powershell\teams_group.js')) {
    Copy-Item -LiteralPath (Join-Path $taskRoot $file) -Destination (Join-Path $packageDir $file) -Force
}
$retiredAdapter = Join-Path $packageDir 'powershell\teams_webview.ps1'
if (Test-Path -LiteralPath $retiredAdapter -PathType Leaf) { Remove-Item -LiteralPath $retiredAdapter -Force }
$keyFile = Join-Path $stateDir '.api_key'
if (-not $Dev -and -not (Test-Path -LiteralPath $keyFile)) {
    $randomBytes = New-Object byte[] 32
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($randomBytes) } finally { $rng.Dispose() }
    [IO.File]::WriteAllText($keyFile, [Convert]::ToBase64String($randomBytes), (New-Object Text.UTF8Encoding $false))
}
$launcher = Join-Path $taskRoot 'run_server.py'
$launchArguments = '"' + $launcher + '"'
if ($Dev) { $launchArguments += ' --dev' }
$started = Start-Process -FilePath $runtimePython -ArgumentList $launchArguments -WorkingDirectory $taskRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $stateDir 'server.stdout.log') -RedirectStandardError (Join-Path $stateDir 'server.stderr.log') -PassThru
[IO.File]::WriteAllText($pidFile, [string]$started.Id)
Write-Output ('Starting Office Action Server; PID ' + $started.Id + '; endpoint http://127.0.0.1:8766/mcp')
