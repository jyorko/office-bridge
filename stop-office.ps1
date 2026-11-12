$ErrorActionPreference = 'Stop'
$pidFile = Join-Path $PSScriptRoot '.state\server.pid'
if (-not (Test-Path -LiteralPath $pidFile)) { Write-Output 'No recorded Office Action Server process.'; exit 0 }
$serverPid = [int](Get-Content -LiteralPath $pidFile)
$proc = Get-CimInstance Win32_Process -Filter "ProcessId = $serverPid"
if ($proc) {
    $expected = Join-Path $PSScriptRoot 'run_server.py'
    if (-not $proc.CommandLine.Contains($expected)) { throw 'PID was reused; refusing to stop an unrelated process.' }
    & taskkill.exe /PID $serverPid /T /F | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not stop the Office Action Server process tree.' }
}
Remove-Item -LiteralPath $pidFile
Write-Output 'Office Action Server stopped.'
