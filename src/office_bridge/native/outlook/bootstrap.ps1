param(
    [Parameter(Mandatory = $true)][string]$PipeName,
    [Parameter(Mandatory = $true)][string]$Generation,
    [Parameter(Mandatory = $true)][int]$ParentPid,
    [switch]$Visible
)
$ErrorActionPreference = 'Stop'
$sdk = Join-Path $env:LOCALAPPDATA 'OfficeActionServer\webview2-sdk\1.0.4314.35'
$env:OFFICE_OUTLOOK_SCRIPT_FILE = Join-Path $PSScriptRoot 'mail.js'
$env:OFFICE_OUTLOOK_SOURCE_HASH = (Get-FileHash -LiteralPath (Join-Path $PSScriptRoot 'OutlookSession.cs') -Algorithm SHA256).Hash.ToLowerInvariant()
Add-Type -AssemblyName System.Windows.Forms
Add-Type -Path (Join-Path $sdk 'Microsoft.Web.WebView2.Core.dll')
Add-Type -Path (Join-Path $PSScriptRoot 'OutlookSession.cs') -ReferencedAssemblies @(
    [System.Windows.Forms.Form].Assembly.Location,
    [System.Drawing.Size].Assembly.Location,
    (Join-Path $sdk 'Microsoft.Web.WebView2.Core.dll'),
    'System.Web.Extensions'
)
[OutlookSession]::Run(
    $sdk,
    (Join-Path $env:LOCALAPPDATA 'OfficeActionServer\outlook-webview2-profile'),
    $PipeName,
    $Generation,
    $ParentPid,
    $Visible.IsPresent
)
