param(
    [switch]$Dev,
    [switch]$SkipCodex,
    [switch]$PrepareOnly
)
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'powershell\bootstrap.ps1') @PSBoundParameters
