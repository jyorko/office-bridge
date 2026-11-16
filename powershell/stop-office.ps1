$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$stateDir = Join-Path $taskRoot '.state'
$recordPath = Join-Path $stateDir 'server.json'
if (-not (Test-Path -LiteralPath $recordPath)) { Write-Output 'No recorded managed Office Action Server process.'; exit 0 }
$record = Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json
$taskData = [IO.Path]::GetFullPath((Join-Path $stateDir 'tasks'))
$actionServer = [IO.Path]::GetFullPath((Join-Path $env:USERPROFILE '.local\bin\action-server.exe'))
if ([IO.Path]::GetFullPath([string]$record.datadir) -ne $taskData -or [IO.Path]::GetFullPath([string]$record.executable) -ne $actionServer -or [string]$record.task -notin @('serve', 'serve-dev')) {
    throw 'The recorded server ownership data is invalid; refusing to stop processes.'
}
$serverPid = [int]$record.pid
$process = Get-CimInstance Win32_Process -Filter "ProcessId = $serverPid" -ErrorAction SilentlyContinue
if ($process) {
    $command = [string]$process.CommandLine
    if ($process.ExecutablePath -ne $actionServer -or -not $command.Contains('devenv') -or -not $command.Contains($taskData) -or -not $command.Contains([string]$record.task)) {
        throw 'Recorded PID no longer belongs to the managed Office Action Server; refusing to stop an unrelated process.'
    }
    $oldActionsHome = $env:ACTIONS_HOME
    $hadActionsHome = Test-Path Env:ACTIONS_HOME
    try {
        Push-Location -LiteralPath $taskRoot
        if ([string]::IsNullOrWhiteSpace($env:ACTIONS_HOME)) { $env:ACTIONS_HOME = Join-Path $env:LOCALAPPDATA 'OfficeActionServer\rcc-actions' }
        & $actionServer devenv --datadir $taskData task stop-host
        if ($LASTEXITCODE -ne 0) { throw 'Managed graceful Teams host shutdown failed; server process tree was left running.' }
    } finally {
        if ($hadActionsHome) { $env:ACTIONS_HOME = $oldActionsHome } else { Remove-Item Env:ACTIONS_HOME -ErrorAction SilentlyContinue }
        Pop-Location
    }
    $kill = Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\taskkill.exe') -ArgumentList @('/PID', [string]$serverPid, '/T', '/F') -WindowStyle Hidden -Wait -PassThru -RedirectStandardOutput (Join-Path $stateDir 'stop.stdout.log') -RedirectStandardError (Join-Path $stateDir 'stop.stderr.log')
    $remaining = Get-CimInstance Win32_Process -Filter "ProcessId = $serverPid" -ErrorAction SilentlyContinue
    if ($kill.ExitCode -ne 0 -and $remaining) { throw 'Could not stop the verified managed server process tree; inspect .state/stop.stderr.log.' }
}
Remove-Item -LiteralPath $recordPath -Force
Remove-Item -LiteralPath (Join-Path $stateDir 'server.pid') -Force -ErrorAction SilentlyContinue
Write-Output 'Office Action Server stopped.'
