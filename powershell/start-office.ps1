param(
    [switch]$Dev,
    [string]$TeamsAccount,
    [switch]$SkipCodex,
    [switch]$SkipCopilot,
    [ValidateRange(0, 65535)][int]$Port = 8766
)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$stateDir = Join-Path $taskRoot '.state'
$taskData = [IO.Path]::GetFullPath((Join-Path $stateDir 'tasks'))
$serverRecord = Join-Path $stateDir 'server.json'
$actionServer = [IO.Path]::GetFullPath((Join-Path $env:USERPROFILE '.local\bin\action-server.exe'))
$taskName = if ($Dev) { 'serve-dev' } else { 'serve' }

function Test-ManagedProcess($Record) {
    if (-not $Record -or [string]$Record.datadir -ne $taskData -or
        [string]$Record.executable -ne $actionServer -or [string]$Record.task -notin @('serve', 'serve-dev')) { return $false }
    $process = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + [int]$Record.pid) -ErrorAction SilentlyContinue
    return ($process -and $process.ExecutablePath -eq $actionServer -and
        $process.CommandLine.Contains('devenv') -and $process.CommandLine.Contains($taskData) -and
        $process.CommandLine.Contains([string]$Record.task))
}

function Test-ManagedListener($Record, [int]$ActualPort) {
    $connections = @(Get-NetTCPConnection -State Listen -LocalPort $ActualPort -ErrorAction SilentlyContinue)
    if (-not $connections.Count) { return $false }
    foreach ($connection in $connections) {
        if ($connection.LocalAddress -ne '127.0.0.1') { return $false }
        $childPid = [int]$connection.OwningProcess
        $seen = @{}
        while ($childPid -and $childPid -ne [int]$Record.pid -and -not $seen.ContainsKey($childPid)) {
            $seen[$childPid] = $true
            $child = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + $childPid) -ErrorAction SilentlyContinue
            if (-not $child) { break }
            $childPid = [int]$child.ParentProcessId
        }
        if ($childPid -ne [int]$Record.pid) { return $false }
    }
    return $true
}

function Save-ServerRecord($Record) {
    $temporary = $serverRecord + '.tmp'
    [IO.File]::WriteAllText($temporary, ($Record | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $temporary -Destination $serverRecord -Force
}

New-Item -ItemType Directory -Path $taskData -Force | Out-Null
$userSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
& icacls.exe $stateDir /inheritance:r /grant:r "*${userSid}:(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not secure the local runtime data directory.' }
if (-not (Test-Path -LiteralPath $actionServer)) { throw 'The official action-server.exe is missing; run bootstrap.ps1.' }

try { $startLock = [IO.File]::Open((Join-Path $stateDir 'server-start.lock'), 'OpenOrCreate', 'ReadWrite', 'None') }
catch { throw 'Office Bridge startup is already in progress; no duplicate server was launched.' }
$savedEnvironment = @{}
foreach ($name in @('ACTIONS_HOME','OFFICE_BRIDGE_TEAMS_ACCOUNT','OFFICE_BRIDGE_PORT','OFFICE_BRIDGE_SKIP_CODEX','OFFICE_BRIDGE_SKIP_COPILOT')) {
    $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
try {
    Push-Location -LiteralPath $taskRoot
    if ([string]::IsNullOrWhiteSpace($env:ACTIONS_HOME)) { $env:ACTIONS_HOME = Join-Path $env:LOCALAPPDATA 'OfficeActionServer\rcc-actions' }
    $record = $null
    if (Test-Path -LiteralPath $serverRecord) {
        $record = Get-Content -LiteralPath $serverRecord -Raw | ConvertFrom-Json
        $existing = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + [int]$record.pid) -ErrorAction SilentlyContinue
        if ($existing -and -not (Test-ManagedProcess $record)) { throw 'Recorded PID belongs to another process; refusing to launch a duplicate.' }
        if ($existing -and $record.task -ne $taskName) { throw 'Office Bridge is running in another reload mode; stop it before changing modes.' }
        if ($existing -and -not $record.PSObject.Properties['instance']) { $record | Add-Member -NotePropertyName instance -NotePropertyValue ([guid]::NewGuid().ToString('N')) }
        if ($existing -and -not $record.PSObject.Properties['mcp_url']) { $record | Add-Member -NotePropertyName mcp_url -NotePropertyValue $null }
        if (-not $existing) { $record = $null }
    }
    if (-not $record) {
        if (-not [string]::IsNullOrWhiteSpace($TeamsAccount)) { $env:OFFICE_BRIDGE_TEAMS_ACCOUNT = $TeamsAccount }
        $prepareTask = if ($Dev) { 'prepare-dev' } else { 'prepare' }
        & $actionServer devenv --datadir $taskData task $prepareTask
        if ($LASTEXITCODE -ne 0) { throw 'Private account configuration could not be prepared.' }
        Remove-Item Env:OFFICE_BRIDGE_TEAMS_ACCOUNT -ErrorAction SilentlyContinue
    }
    $stderrPath = Join-Path $stateDir 'server.stderr.log'
    for ($attempt = 0; $attempt -lt 2; $attempt++) {
        if (-not $record) {
            $env:OFFICE_BRIDGE_PORT = if ($attempt -eq 0) { [string]$Port } else { '0' }
            $arguments = 'devenv --datadir "' + $taskData + '" task ' + $taskName
            $started = Start-Process -FilePath $actionServer -ArgumentList $arguments -WorkingDirectory $taskRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $stateDir 'server.stdout.log') -RedirectStandardError $stderrPath -PassThru
            $record = @{ pid = $started.Id; task = $taskName; datadir = $taskData; executable = $actionServer; instance = [guid]::NewGuid().ToString('N'); mcp_url = $null; requested_port = $Port; startup_attempt = $attempt + 1 }
            Save-ServerRecord $record
            [IO.File]::WriteAllText((Join-Path $stateDir 'server.pid'), [string]$started.Id)
            Write-Output ('Starting Office Bridge; PID ' + $started.Id + '. Waiting for the bound endpoint...')
        }
        $deadline = [DateTime]::UtcNow.AddSeconds(600)
        $ready = $false
        $logText = ''
        while ([DateTime]::UtcNow -lt $deadline) {
            if (-not (Test-ManagedProcess $record)) { break }
            if (Test-Path -LiteralPath $stderrPath) {
                $logText = [string](Get-Content -LiteralPath $stderrPath -Raw)
                $clean = [regex]::Replace($logText, '\x1B\[[0-?]*[ -/]*[@-~]', '')
                $match = [regex]::Match($clean, 'Local MCP endpoint:\s*(http://127\.0\.0\.1:([0-9]{1,5})/mcp)(?:\s|$)')
                if ($match.Success -and (Test-ManagedListener $record ([int]$match.Groups[2].Value))) {
                    $record.mcp_url = $match.Groups[1].Value
                    Save-ServerRecord $record
                    $ready = $true
                    break
                }
            }
            Start-Sleep -Seconds 1
        }
        if ($ready) { break }
        if (Test-Path -LiteralPath $stderrPath) { $logText = [string](Get-Content -LiteralPath $stderrPath -Raw) }
        if ($attempt -eq 0 -and -not (Test-ManagedProcess $record) -and $logText -match '(?i)address already in use|WinError 10048|WinError 10013|error while attempting to bind') {
            $record = $null
            continue
        }
        throw 'Office Bridge did not publish an owned listener; inspect .state/server.stderr.log. No client configuration was changed.'
    }
    Write-Output ('Office Bridge: ' + ([string]$record.mcp_url).Replace('/mcp', '/') + ' | MCP: ' + $record.mcp_url)
    $verifyTask = if ($Dev) { 'verify-dev' } else { 'verify' }
    & $actionServer devenv --datadir $taskData task $verifyTask
    if ($LASTEXITCODE -ne 0) { throw 'Office Bridge read-only verification failed; clients were not registered.' }
    $env:OFFICE_BRIDGE_SKIP_CODEX = if ($SkipCodex) { '1' } else { '0' }
    $env:OFFICE_BRIDGE_SKIP_COPILOT = if ($SkipCopilot) { '1' } else { '0' }
    if (-not ($SkipCodex -and $SkipCopilot)) {
        $registerTask = if ($Dev) { 'register-dev' } else { 'register' }
        & $actionServer devenv --datadir $taskData task $registerTask
        if ($LASTEXITCODE -ne 0) { throw 'MCP registration failed; inspect the reported client configuration.' }
    }
} finally {
    foreach ($name in $savedEnvironment.Keys) { [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], 'Process') }
    $startLock.Dispose()
    Pop-Location
}
