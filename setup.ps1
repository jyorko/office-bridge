$ErrorActionPreference = 'Stop'
$uvExe = (Get-Command uv.exe -ErrorAction Stop).Source
$runtimePython = Join-Path $PSScriptRoot '.runtime\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $runtimePython)) {
    & $uvExe --system-certs venv --python 3.12 (Join-Path $PSScriptRoot '.runtime')
    if ($LASTEXITCODE -ne 0) { throw 'Runtime environment creation failed.' }
}
& $uvExe --system-certs pip install --python $runtimePython 'actions-runtime==1.0.4' 'actions-core==1.0.3' 'pydantic==2.14.3' 'robocorp-browser==2.4.0' 'pytest==8.3.5'
if ($LASTEXITCODE -ne 0) { throw 'Runtime dependency installation failed.' }
Write-Output 'Runtime installed. Run start-office.ps1 to build the RCC worker environment and start the server.'
