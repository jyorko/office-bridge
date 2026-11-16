param(
    [switch]$Dev,
    [switch]$SkipCodex,
    [switch]$SkipCopilot,
    [switch]$PrepareOnly,
    [string]$TeamsAccount,
    [ValidateRange(0, 65535)][int]$Port = 8766
)
$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    & (Join-Path $PSScriptRoot 'powershell\bootstrap.ps1') @PSBoundParameters
} finally {
    Pop-Location
}
