$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot

$uv = Get-Command uv.exe -ErrorAction SilentlyContinue
if (-not $uv) {
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $winget) {
        throw 'uv is required but was not found. Install uv from https://docs.astral.sh/uv/getting-started/installation/ and rerun bootstrap.ps1.'
    }
    & $winget.Source install --id astral-sh.uv --exact --source winget --silent --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) { throw 'winget could not install uv. Install uv from https://docs.astral.sh/uv/getting-started/installation/ and rerun.' }
    $uv = Get-Command uv.exe -ErrorAction SilentlyContinue
    if (-not $uv) {
        $candidate = Join-Path $env:USERPROFILE '.local\bin\uv.exe'
        if (Test-Path -LiteralPath $candidate) { $uv = @{ Source = $candidate } }
    }
    if (-not $uv) { throw 'uv installed, but this PowerShell session cannot find uv.exe. Open a new PowerShell window and rerun bootstrap.ps1.' }
}

$runtimeDir = Join-Path $taskRoot '.runtime'
$runtimePython = Join-Path $runtimeDir 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $runtimePython)) {
    & $uv.Source --system-certs venv --python 3.12 $runtimeDir
    if ($LASTEXITCODE -ne 0) { throw 'Runtime environment creation failed. Confirm Python 3.12 is available through uv and retry.' }
}
& $uv.Source --system-certs pip install --python $runtimePython --requirement (Join-Path $taskRoot 'scripts\requirements-dev.txt')
if ($LASTEXITCODE -ne 0) { throw 'Runtime dependency installation failed.' }
$exeInstaller = Join-Path $taskRoot 'scripts\install_runtime_exe.py'
& $runtimePython $exeInstaller
if ($LASTEXITCODE -ne 0) { throw 'Actions Runtime executable installation failed.' }
Write-Output 'Pinned Python runtime installed.'
