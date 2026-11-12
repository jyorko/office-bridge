$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$pidFile = Join-Path $taskRoot '.state\server.pid'
if (-not (Test-Path -LiteralPath $pidFile)) { Write-Output 'No recorded Office Action Server process.'; exit 0 }
$serverPid = [int](Get-Content -LiteralPath $pidFile)
$proc = Get-CimInstance Win32_Process -Filter "ProcessId = $serverPid"
if ($proc) {
    $expected = Join-Path $taskRoot 'scripts\run_server.py'
    if (-not $proc.CommandLine.Contains($expected)) { throw 'PID was reused; refusing to stop an unrelated process.' }
    $runtimePython = Join-Path $taskRoot '.runtime\Scripts\python.exe'
    if (Test-Path -LiteralPath $runtimePython) { & $runtimePython (Join-Path $taskRoot 'scripts\stop_teams_session.py') }
    $kill = Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\taskkill.exe') -ArgumentList @('/PID', [string]$serverPid, '/T', '/F') -WindowStyle Hidden -Wait -PassThru -RedirectStandardOutput (Join-Path $taskRoot '.state\stop.stdout.log') -RedirectStandardError (Join-Path $taskRoot '.state\stop.stderr.log')
    $remaining = Get-CimInstance Win32_Process -Filter "ProcessId = $serverPid"
    if ($kill.ExitCode -ne 0 -and $remaining) { throw 'Could not stop the Office Action Server process tree; inspect .state/stop.stderr.log.' }
}
Remove-Item -LiteralPath $pidFile
Write-Output 'Office Action Server stopped.'
