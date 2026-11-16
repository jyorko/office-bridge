param(
    [switch]$Dev,
    [switch]$SkipCodex,
    [switch]$PrepareOnly,
    [string]$TeamsAccount
)
$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    & (Join-Path $PSScriptRoot 'powershell\bootstrap.ps1') @PSBoundParameters
} finally {
    Pop-Location
}
