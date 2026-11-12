param([switch]$Dev, [string]$TeamsAccount)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
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
    if ($recorded -and $recorded.CommandLine.Contains((Join-Path $taskRoot 'scripts\run_server.py'))) {
        Write-Output 'Office Action Server is already listening on http://127.0.0.1:8766/mcp'
        exit 0
    }
    throw 'Port 8766 belongs to another process.'
}
$prepareArguments = @((Join-Path $taskRoot 'scripts\prepare_state.py'))
if ($Dev) { $prepareArguments += '--dev' }
if (-not [string]::IsNullOrWhiteSpace($TeamsAccount)) { $prepareArguments += @('--teams-account', $TeamsAccount) }
& $runtimePython @prepareArguments
if ($LASTEXITCODE -ne 0) { throw 'Private device configuration could not be prepared.' }
$launcher = Join-Path $taskRoot 'scripts\run_server.py'
$launchArguments = '"' + $launcher + '"'
if ($Dev) { $launchArguments += ' --dev' }
$started = Start-Process -FilePath $runtimePython -ArgumentList $launchArguments -WorkingDirectory $taskRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $stateDir 'server.stdout.log') -RedirectStandardError (Join-Path $stateDir 'server.stderr.log') -PassThru
[IO.File]::WriteAllText($pidFile, [string]$started.Id)
Write-Output ('Starting Office Action Server; PID ' + $started.Id + '; endpoint http://127.0.0.1:8766/mcp')
