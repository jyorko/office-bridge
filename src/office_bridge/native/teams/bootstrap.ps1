param([Parameter(Mandatory=$true)][string]$PipeName,[Parameter(Mandatory=$true)][string]$Generation,[Parameter(Mandatory=$true)][int]$ParentPid)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'snapshot.ps1')
$codeSnapshot = New-TeamsCodeSnapshot -Source $PSScriptRoot -Generation $Generation
$env:OFFICE_TEAMS_SCRIPT_FILE = Join-Path $codeSnapshot 'chat.js'
$env:OFFICE_TEAMS_HOST_SOURCE = Join-Path $codeSnapshot 'TeamsSession.cs'
$sdk=Join-Path $env:LOCALAPPDATA 'OfficeActionServer\webview2-sdk\1.0.4314.35'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -Path (Join-Path $sdk 'Microsoft.Web.WebView2.Core.dll')
Add-Type -Path (Join-Path $codeSnapshot 'TeamsSession.cs') -ReferencedAssemblies @([System.Windows.Forms.Form].Assembly.Location,[System.Drawing.Size].Assembly.Location,(Join-Path $sdk 'Microsoft.Web.WebView2.Core.dll'),'System.Web.Extensions')
[TeamsSession]::Run($sdk,(Join-Path $env:LOCALAPPDATA 'OfficeActionServer\teams-webview2-profile'),$PipeName,$Generation,$ParentPid)
