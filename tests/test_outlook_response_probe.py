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
if ((Filter 'https://outlook.cloud.microsoft/owa/me/service.svc?action=GetConversationItems') -ne 'GetConversationItems') { throw 'Current mailbox path rejected' }
if ((Filter 'https://outlook.office.com/api/beta/me/Microsoft.OutlookServices.GetConversationItems') -ne 'RestItems') { throw 'REST conversation route rejected' }
if ((Filter 'https://outlook.office.com/ows/v1.0/mailbox/items/message') -ne 'RestItems') { throw 'REST item route rejected' }
if ((Filter 'https://outlook.office.com/owa/service.svc?app=Mail') -ne 'MailService') { throw 'Unlabelled mail RPC route rejected' }
foreach ($action in @('GetItem','CreateItem','UpdateItem','FindItem','MoveItem','SendItem')) {
    if ((Filter ('https://outlook.office.com/owa/service.svc?action='+$action)) -cne $action) { throw 'Item action rejected' }
}
foreach ($url in @(
    'https://outlook.office.com/owa/service.svc?action=DeleteItem',
    'https://example.invalid/owa/service.svc?action=GetConversationItems',
    'https://outlook.office.com.evil.invalid/owa/service.svc?action=GetConversationItems',
    'https://outlook.office.com:444/owa/service.svc?action=GetConversationItems',
    'https://user@outlook.office.com/owa/service.svc?action=GetConversationItems',
    'http://outlook.office.com/owa/service.svc?action=GetConversationItems',
    'https://outlook.office.com/other?action=GetConversationItems',
    'https://outlook.office.com/owa/a/b/service.svc?action=GetConversationItems',
    'https://outlook.office.com/api/beta/me/messages/item/attachments/file/$value',
    'https://outlook.office.com/owa/service.svc?action=GetConversationItems&action=GetItem'
)) {
    if ((Filter $url) -ne '') { throw 'Unexpected action accepted' }
}
$probe = [Runtime.Serialization.FormatterServices]::GetUninitializedObject($type)
$type.GetField('responseProbeConversation', $instance).SetValue($probe, 'conversation-a')
$type.GetField('responseItemFields', $instance).SetValue($probe, (New-Object 'Collections.Generic.HashSet[string]'))
$type.GetField('responseIdentityFields', $instance).SetValue($probe, (New-Object 'Collections.Generic.HashSet[string]'))
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
$parse = $type.GetMethod('DeserializeMailResponse', $static)
$reduce = $type.GetMethod('ObservedItems', $static)
$response = '{"__type":"GetConversationItemsJsonResponse:#Exchange","Body":{"__type":"GetConversationItemsResponse:#Exchange","ConversationId":{"Id":"conversation-a"},"Items":[{"__type":"Message:#Exchange","ItemId":{"Id":"item-a","ChangeKey":"version-a"},"IsDraft":false,"HasAttachments":false,"ToRecipients":[{"Mailbox":{"EmailAddress":"self@example.test","Name":"Self"}}],"CcRecipients":[],"Body":{"Value":"literal {\"__type\":\"unchanged\"}"}}]}}'
$parsed = $parse.Invoke($null, @($response))
$records = $reduce.Invoke($null, @($parsed))
if ($records.Count -ne 1 -or $records['conversation-a'].Count -ne 1) { throw 'Parent conversation identity was lost' }
$record = $records['conversation-a'][0]
if ($record['entry_id'] -cne 'item-a' -or -not $record['complete'] -or $record['is_draft']) { throw 'Typed response was not reduced' }
if ($json.Serialize($records).Contains('unchanged')) { throw 'Response body retained' }
if ($parsed['Body']['Items'][0]['Body']['Value'] -cne 'literal {"__type":"unchanged"}') { throw 'Body text changed during parsing' }
$graph = $parse.Invoke($null, @('{"id":"graph-item","changeKey":"graph-version","conversationId":"graph-conversation","isDraft":false,"hasAttachments":false,"toRecipients":[{"emailAddress":{"name":"Self","address":"self@example.test"}}],"ccRecipients":[],"from":{"emailAddress":{"name":"Sender","address":"sender@example.test"}}}'))
$records = $reduce.Invoke($null, @($graph))
$record = $records['graph-conversation'][0]
if (-not $record['complete'] -or $record['recipients'][0]['smtp_address'] -cne 'self@example.test' -or $record['sender']['smtp_address'] -cne 'sender@example.test') { throw 'REST metadata was not reduced' }
$withoutVersion = $parse.Invoke($null, @('{"id":"known-item","conversationId":"known-conversation","isDraft":false,"hasAttachments":false,"toRecipients":[],"ccRecipients":[]}'))
$records = $reduce.Invoke($null, @($withoutVersion))
if ($records['known-conversation'][0]['complete'] -or -not $records['known-conversation'][0]['draft_known']) { throw 'Missing change key was invented' }
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
