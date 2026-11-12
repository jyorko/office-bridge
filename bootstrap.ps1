param(
    [switch]$Dev,
    [switch]$SkipCodex,
    [switch]$PrepareOnly,
    [string]$TeamsAccount
)
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'powershell\bootstrap.ps1') @PSBoundParameters
