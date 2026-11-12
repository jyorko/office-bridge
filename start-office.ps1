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
foreach ($file in @('package.yaml', 'office_actions.py', 'attachment_actions.py', 'teams_web_actions.py', 'teams_web_backend.py', 'desktop_bridge.py', 'powershell\office.ps1', 'powershell\attachments.ps1')) {
    Copy-Item -LiteralPath (Join-Path $taskRoot $file) -Destination (Join-Path $packageDir $file) -Force
}
$keyFile = Join-Path $stateDir '.api_key'
if (-not (Test-Path -LiteralPath $keyFile)) {
    $randomBytes = New-Object byte[] 32
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($randomBytes) } finally { $rng.Dispose() }
    [IO.File]::WriteAllText($keyFile, [Convert]::ToBase64String($randomBytes), (New-Object Text.UTF8Encoding $false))
}
$launcher = Join-Path $taskRoot 'run_server.py'
$started = Start-Process -FilePath $runtimePython -ArgumentList ('"' + $launcher + '"') -WorkingDirectory $taskRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $stateDir 'server.stdout.log') -RedirectStandardError (Join-Path $stateDir 'server.stderr.log') -PassThru
[IO.File]::WriteAllText($pidFile, [string]$started.Id)
Write-Output ('Starting Office Action Server; PID ' + $started.Id + '; endpoint http://127.0.0.1:8766/mcp')
