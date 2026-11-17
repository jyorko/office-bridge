param([switch]$HelpersOnly)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $root 'src\office_bridge\native\rcc-trust.ps1')
if (-not $HelpersOnly) { Invoke-RccBootstrap -Root $root }
