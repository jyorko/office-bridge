param(
    [Parameter(Mandatory=$true)][string]$Sdk,
    [Parameter(Mandatory=$true)][string]$Profile,
    [Parameter(Mandatory=$true)][string]$ViewId,
    [Parameter(Mandatory=$true)][string]$Digest,
    [Parameter(Mandatory=$true)][int]$Count,
    [switch]$VerifyHidden
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -Path (Join-Path $Sdk 'Microsoft.Web.WebView2.Core.dll')
Add-Type -Path (Join-Path $PSScriptRoot 'CanvasWindow.cs') -ReferencedAssemblies @(
    [System.Windows.Forms.Form].Assembly.Location,
    [System.Drawing.Size].Assembly.Location,
    (Join-Path $Sdk 'Microsoft.Web.WebView2.Core.dll'),
    'System.Core'
)
[CanvasWindow]::Run($Sdk, $Profile, $ViewId, $Digest, $Count, $VerifyHidden.IsPresent)
