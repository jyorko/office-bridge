$ErrorActionPreference = 'Stop'
$uvExe = (Get-Command uv.exe -ErrorAction Stop).Source
$runtimePython = Join-Path $PSScriptRoot '.runtime\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $runtimePython)) {
    & $uvExe --system-certs venv --python 3.12 (Join-Path $PSScriptRoot '.runtime')
    if ($LASTEXITCODE -ne 0) { throw 'Runtime environment creation failed.' }
}
& $uvExe --system-certs pip install --python $runtimePython --requirement (Join-Path $PSScriptRoot 'scripts\requirements-dev.txt')
if ($LASTEXITCODE -ne 0) { throw 'Runtime dependency installation failed.' }
Write-Output 'Runtime installed. Run start-office.ps1 to build the RCC worker environment and start the server.'
