param([switch]$Dev, [string]$TeamsAccount)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$stateDir = Join-Path $taskRoot '.state'
$taskData = [IO.Path]::GetFullPath((Join-Path $stateDir 'tasks'))
$serverRecord = Join-Path $stateDir 'server.json'
$userSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
New-Item -ItemType Directory -Path $taskData -Force | Out-Null
& icacls.exe $stateDir /inheritance:r /grant:r "*${userSid}:(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not secure the local runtime data directory.' }

$listener = Get-NetTCPConnection -State Listen -LocalPort 8766 -ErrorAction SilentlyContinue
if ($listener) {
    if (Test-Path -LiteralPath $serverRecord) {
        $record = Get-Content -LiteralPath $serverRecord -Raw | ConvertFrom-Json
        $process = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + [int]$record.pid) -ErrorAction SilentlyContinue
        $exe = Join-Path $env:USERPROFILE '.local\bin\action-server.exe'
        if ($process -and $process.ExecutablePath -eq $exe -and $process.CommandLine.Contains('devenv') -and $process.CommandLine.Contains($taskData) -and $process.CommandLine.Contains([string]$record.task)) {
            $listenerOwned = $true
            foreach ($connection in $listener) {
                $listenerPid = [int]$connection.OwningProcess
                $seen = @{}
                while ($listenerPid -and $listenerPid -ne [int]$record.pid -and -not $seen.ContainsKey($listenerPid)) {
                    $seen[$listenerPid] = $true
                    $child = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + $listenerPid) -ErrorAction SilentlyContinue
                    if (-not $child) { break }
                    $listenerPid = [int]$child.ParentProcessId
                }
                if ($listenerPid -ne [int]$record.pid) { $listenerOwned = $false; break }
            }
            if ($listenerOwned) {
                Write-Output 'Office Action Server is already listening on http://127.0.0.1:8766/mcp'
                exit 0
            }
        }
    }
    throw 'Port 8766 is occupied by a process that is not the recorded managed Office Action Server.'
}
if (Test-Path -LiteralPath $serverRecord) {
    $record = Get-Content -LiteralPath $serverRecord -Raw | ConvertFrom-Json
    $process = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + [int]$record.pid) -ErrorAction SilentlyContinue
    $exe = [IO.Path]::GetFullPath((Join-Path $env:USERPROFILE '.local\bin\action-server.exe'))
    if ($process -and $process.ExecutablePath -eq $exe -and $process.CommandLine.Contains('devenv') -and $process.CommandLine.Contains($taskData) -and $process.CommandLine.Contains([string]$record.task)) {
        Write-Output ('Office Action Server task ' + $record.task + ' is already starting; PID ' + $record.pid + '.')
        exit 0
    }
    if ($process) { throw 'The recorded server PID is active but does not match the managed Action Server task; refusing to start a duplicate.' }
}

$actionServer = Join-Path $env:USERPROFILE '.local\bin\action-server.exe'
if (-not (Test-Path -LiteralPath $actionServer)) { throw 'The official action-server.exe is missing; run bootstrap.ps1 to install it.' }
$taskName = if ($Dev) { 'serve-dev' } else { 'serve' }
$prepareTask = if ($Dev) { 'prepare-dev' } else { 'prepare' }
$oldActionsHome = $env:ACTIONS_HOME
$hadActionsHome = Test-Path Env:ACTIONS_HOME
$oldTeamsAccount = $env:OFFICE_BRIDGE_TEAMS_ACCOUNT
$hadTeamsAccount = Test-Path Env:OFFICE_BRIDGE_TEAMS_ACCOUNT
try {
    if ([string]::IsNullOrWhiteSpace($env:ACTIONS_HOME)) { $env:ACTIONS_HOME = Join-Path $env:LOCALAPPDATA 'OfficeActionServer\rcc-actions' }
    if (-not [string]::IsNullOrWhiteSpace($TeamsAccount)) { $env:OFFICE_BRIDGE_TEAMS_ACCOUNT = $TeamsAccount }
    & $actionServer devenv --datadir $taskData task $prepareTask
    if ($LASTEXITCODE -ne 0) { throw 'Private device configuration could not be prepared by the managed task.' }
    Remove-Item Env:OFFICE_BRIDGE_TEAMS_ACCOUNT -ErrorAction SilentlyContinue
    $arguments = 'devenv --datadir "' + $taskData + '" task ' + $taskName
    $started = Start-Process -FilePath $actionServer -ArgumentList $arguments -WorkingDirectory $taskRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $stateDir 'server.stdout.log') -RedirectStandardError (Join-Path $stateDir 'server.stderr.log') -PassThru
    $record = @{ pid = $started.Id; task = $taskName; datadir = $taskData; executable = [IO.Path]::GetFullPath($actionServer) }
    $temporaryRecord = $serverRecord + '.tmp'
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($temporaryRecord, ($record | ConvertTo-Json), $utf8NoBom)
    Move-Item -LiteralPath $temporaryRecord -Destination $serverRecord -Force
    [IO.File]::WriteAllText((Join-Path $stateDir 'server.pid'), [string]$started.Id, $utf8NoBom)
    Write-Output ('Starting Office Action Server; PID ' + $started.Id + '; endpoint http://127.0.0.1:8766/mcp')
} finally {
    if ($hadActionsHome) { $env:ACTIONS_HOME = $oldActionsHome } else { Remove-Item Env:ACTIONS_HOME -ErrorAction SilentlyContinue }
    if ($hadTeamsAccount) { $env:OFFICE_BRIDGE_TEAMS_ACCOUNT = $oldTeamsAccount } else { Remove-Item Env:OFFICE_BRIDGE_TEAMS_ACCOUNT -ErrorAction SilentlyContinue }
}
