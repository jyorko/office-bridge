"""Exercise the native passive-response filter without a WebView or mailbox."""

import os
import subprocess
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "src/office_bridge/native/outlook/OutlookSession.cs"
SDK = (
    Path(os.environ.get("LOCALAPPDATA", ""))
    / "OfficeActionServer/webview2-sdk/1.0.4314.35/Microsoft.Web.WebView2.Core.dll"
)


@pytest.mark.skipif(os.name != "nt" or not SDK.is_file(), reason="Windows WebView2 SDK required")
def test_native_response_filter_and_synthetic_shape(tmp_path):
    script = tmp_path / "probe-test.ps1"
    script.write_text(
        r"""
param([string]$Source, [string]$Sdk)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -Path $Sdk
Add-Type -Path $Source -ReferencedAssemblies @(
    [System.Windows.Forms.Form].Assembly.Location,
    [System.Drawing.Size].Assembly.Location,
    $Sdk,
    'System.Web.Extensions'
)
$type = [OutlookSession]
$static = [Reflection.BindingFlags]'Static,NonPublic'
$instance = [Reflection.BindingFlags]'Instance,NonPublic'
$filter = $type.GetMethod('MailResponseAction', $static)
function Filter([string]$url) { $filter.Invoke($null, @($url)) }
if ((Filter 'https://outlook.office.com/owa/service.svc?action=GetConversationItems') -ne 'GetConversationItems') { throw 'Allowlisted action rejected' }
if ((Filter 'https://outlook.cloud.microsoft/owa/0/service.svc?action=GetConversationItems') -ne 'GetConversationItems') { throw 'Numbered mailbox path rejected' }
foreach ($url in @(
    'https://outlook.office.com/owa/service.svc?action=GetItem',
    'https://example.invalid/owa/service.svc?action=GetConversationItems',
    'https://outlook.office.com.evil.invalid/owa/service.svc?action=GetConversationItems',
    'https://outlook.office.com:444/owa/service.svc?action=GetConversationItems',
    'https://user@outlook.office.com/owa/service.svc?action=GetConversationItems',
    'http://outlook.office.com/owa/service.svc?action=GetConversationItems',
    'https://outlook.office.com/other?action=GetConversationItems',
    'https://outlook.office.com/owa/service.svc?action=GetConversationItems&action=GetItem'
)) {
    if ((Filter $url) -ne '') { throw 'Unexpected action accepted' }
}
$probe = [Runtime.Serialization.FormatterServices]::GetUninitializedObject($type)
$type.GetField('responseProbeConversation', $instance).SetValue($probe, 'conversation-a')
$json = New-Object System.Web.Script.Serialization.JavaScriptSerializer
$result = $json.DeserializeObject('{"ResponseMessages":[{"ConversationId":{"Id":"conversation-a"},"Items":[{"ItemId":{"Id":"item-a","ChangeKey":"version-a"},"Body":{"Value":"synthetic"}}]},{"ConversationId":{"Id":"conversation-b"}}]}')
$type.GetMethod('CountResponseShape', $instance).Invoke($probe, @($result))
foreach ($pair in @{
    responseProbeItemIds=1
    responseProbeConversationIds=2
    responseProbeConversationMatches=1
    responseProbeChangeKeys=1
    responseProbeBodies=1
}.GetEnumerator()) {
    if ($type.GetField($pair.Key, $instance).GetValue($probe) -ne $pair.Value) {
        throw 'Unexpected shape count'
    }
}
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(script),
            "-Source",
            str(SOURCE),
            "-Sdk",
            str(SDK),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stderr
