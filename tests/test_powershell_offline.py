"""Run only extracted pure helper functions against synthetic objects, never Office."""

import subprocess
from pathlib import Path


def test_powershell_identity_version_digest_guards(tmp_path):
    source = Path(__file__).parents[1] / "src" / "office_bridge" / "native" / "office.ps1"
    harness = tmp_path / "synthetic.ps1"
    harness.write_text(
        r"""
param([string]$SourceFile)
$ErrorActionPreference='Stop'
$tokens=$null;$errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile($SourceFile,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'PowerShell syntax errors'}
# Extract function definitions only. The dispatcher, COM startup and UI calls never execute.
$functions=$ast.FindAll({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst]},$false)
$functions | ForEach-Object {. ([scriptblock]::Create($_.Extent.Text))}
$utf8=New-Object System.Text.UTF8Encoding($false)
function Assert-Failure([scriptblock]$code,[string]$expectedCode){
 $caught=$false
 try{& $code}catch{if($_.Exception.Message -cne $expectedCode){throw};$caught=$true}
 if(-not $caught){throw "Expected $expectedCode"}
}
$parent=[pscustomobject]@{EntryID='inbox';StoreID='store-A'}
$mail=[pscustomobject]@{EntryID='entry';Class=43;Parent=$parent;LastModificationTime=[datetime]'2026-11-12T12:00:00Z';Subject='Subject';UnRead=$true;FlagStatus=0;Importance=1}
$ns=[pscustomobject]@{Item=$mail}
$ns | Add-Member ScriptMethod GetItemFromID {param($entry,$store) return $this.Item}
$valid=[pscustomobject]@{entry_id='entry';store_id='store-A'}
if((Get-Mail $valid).EntryID -cne 'entry'){throw 'Good identity rejected'}
Assert-Failure {Get-Mail ([pscustomobject]@{entry_id='entry';store_id='other-store'})} 'not_found'
$expected=[pscustomobject](Version $mail)
Check-Version $mail $expected
$mail.Subject='Changed by user'
Assert-Failure {Check-Version $mail $expected} 'stale_message'
$mail.Subject='Subject';$mail.FlagStatus=2
Assert-Failure {Check-Version $mail $expected} 'stale_message'
$mail.FlagStatus=0;$mail.LastModificationTime=$mail.LastModificationTime.AddSeconds(1)
Assert-Failure {Check-Version $mail $expected} 'stale_message'
$entry=[pscustomobject]@{Type='SMTP';Address='user@example.test'}
$recipient=[pscustomobject]@{Name='Recipient';Type=1;AddressEntry=$entry}
$recipients=[pscustomobject]@{Count=1;Recipient=$recipient}
$recipients | Add-Member ScriptMethod Item {param($index) return $this.Recipient}
$draft=[pscustomobject]@{Subject='Draft';Body='Body';Recipients=$recipients;BodyFormat=1;Attachments=[pscustomobject]@{Count=0};SendUsingAccount=[pscustomobject]@{SmtpAddress='sender@example.test'};SentOnBehalfOfName=''}
$digest=Digest $draft
if($digest -notmatch '^[a-f0-9]{64}$'){throw 'Invalid digest'}
$draft.Body='Edited';if((Digest $draft) -ceq $digest){throw 'Body edit undetected'}
$draft.Body='Body';$recipient.Type=2;if((Digest $draft) -ceq $digest){throw 'Recipient role edit undetected'}
$recipient.Type=1;$entry.Address='other@example.test';if((Digest $draft) -ceq $digest){throw 'Recipient edit undetected'}
$entry.Address='unresolved';Assert-Failure {Digest $draft} 'unresolved_recipient'
$entry.Address='user@example.test';$draft.BodyFormat=2;Assert-Failure {Digest $draft} 'stale_draft'
$draft.BodyFormat=1;$draft.Attachments.Count=1;Assert-Failure {Digest $draft} 'stale_draft'
$draft.Attachments.Count=0;$draft.SendUsingAccount.SmtpAddress='other@example.test'
if((Digest $draft) -ceq $digest){throw 'Sender account edit undetected'}
$fallback=[pscustomobject]@{SmtpAddress='sender@example.test';DeliveryStore=[pscustomobject]@{StoreID='store-A'}}
$ns | Add-Member NoteProperty Accounts @($fallback)
$draft | Add-Member NoteProperty Parent ([pscustomobject]@{StoreID='store-A'})
$draft.SendUsingAccount=$null
if((Digest $draft) -cne $digest){throw 'Reopened draft account fallback changed digest'}
$ns.Accounts=@($fallback,$fallback)
Assert-Failure {Digest $draft} 'stale_draft'
$ns.Accounts=@([pscustomobject]@{SmtpAddress='other@example.test';DeliveryStore=[pscustomobject]@{StoreID='store-A'}})
if((Digest $draft) -ceq $digest){throw 'Fallback account identity change undetected'}
if((Chat-Name 'Chat Briggs, Gary Out of office Last message text') -cne 'Briggs, Gary'){throw 'DM normalization failed'}
if((Chat-Name 'Group chat QA-ASSIST-VERINT-DEPLOY Has pinned messages Last message text') -cne 'QA-ASSIST-VERINT-DEPLOY'){throw 'Group normalization failed'}
if($null -ne (Chat-Name 'Favorites')){throw 'Category accepted as chat'}
if((Chat-Name 'Chat Person In a meeting Last message text') -cne 'Person'){throw 'Meeting presence normalization failed'}
if((Chat-Name 'Chat Yorko, Joshua (You) You: synthetic') -cne 'Yorko, Joshua (You)'){throw 'Self chat without Last message rejected'}
Assert-SelfAccount 'joshua.yorko@gainwelltechnologies.com' 'joshua.yorko@gainwelltechnologies.com'
Assert-Failure {Assert-SelfAccount 'joshua.yorko@gainwelltechnologies.com' 'other@example.test'} 'self_identity_unverified'
Assert-Failure {Assert-SelfAccount 'other@example.test' 'other@example.test'} 'self_identity_unverified'
$selfWindow=[pscustomobject]@{Current=[pscustomobject]@{Name='Chat | Yorko, Joshua (You) | Gainwell Technologies | joshua.yorko@gainwelltechnologies.com | Microsoft Teams'}}
Assert-SelfWindow $selfWindow
$selfWindow.Current.Name='Chat | Other Person | Gainwell Technologies | joshua.yorko@gainwelltechnologies.com | Microsoft Teams'
Assert-Failure {Assert-SelfWindow $selfWindow} 'self_identity_unverified'
Assert-NotEditing 123 999 $true
Assert-NotEditing 123 123 $false
Assert-Failure {Assert-NotEditing 123 123 $true} 'desktop_busy'
Assert-NavigationAllowed 123 999 $true
Assert-NavigationAllowed 123 123 $false
Assert-Failure {Assert-NavigationAllowed 123 999 $false} 'navigation_requires_foreground'
# Fake every external dependency: these tests never launch or close an application.
function Teams-Windows {return $script:fakeWindows}
function Get-Process {param($Name,$ErrorAction) return $script:fakeProcesses}
function Get-AppxPackage {param($Name,$ErrorAction) return @([pscustomobject]@{InstallLocation='C:\SyntheticTeams'})}
function Test-Path {param($LiteralPath,$PathType) return $true}
function Start-Sleep {param($Milliseconds)}
function Start-Process {param($FilePath,$WindowStyle)
 if($FilePath -cne 'C:\SyntheticTeams\ms-teams.exe' -or $WindowStyle -cne 'Minimized'){throw 'Unsafe Teams launch'}
 $script:launched=$true;$script:fakeWindows=@([pscustomobject]@{TestWindow=$true})
}
$script:fakeWindows=@([pscustomobject]@{TestWindow=$true});$script:fakeProcesses=@();$script:launched=$false
[void](Get-Teams);if($script:launched){throw 'Existing main window launched another instance'}
$script:fakeWindows=@();$script:fakeProcesses=@([pscustomobject]@{Id=123})
Assert-Failure {Get-Teams} 'app_not_ready';if($script:launched){throw 'Running meeting triggered launch'}
$script:fakeProcesses=@();[void](Get-Teams);if(-not $script:launched){throw 'Absent Teams did not launch'}
$script:fakeWindows=@([pscustomobject]@{},[pscustomobject]@{})
Assert-Failure {Get-Teams} 'ambiguous_chat'
$selfWindow.Current.Name='Chat | Yorko, Joshua (You) | Gainwell Technologies | other@example.test | Microsoft Teams'
Assert-Failure {Assert-SelfWindow $selfWindow} 'self_identity_unverified'
# UIA exposes structural nodes with null AutomationId; they must be skipped.
function Elements($window){return @(
 [pscustomobject]@{Current=[pscustomobject]@{AutomationId=$null;Name='container'}},
 [pscustomobject]@{Current=[pscustomobject]@{AutomationId='';Name='empty id'}},
 [pscustomobject]@{Current=[pscustomobject]@{AutomationId='message-body-123';Name='synthetic text'}}
)}
$bodies=@(Bodies $null)
if($bodies.Count -ne 1 -or $bodies[0].observed_id -cne 'message-body-123'){throw 'Null UIA ID filtering failed'}
'synthetic guards passed'
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(harness),
            "-SourceFile",
            str(source),
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert "synthetic guards passed" in result.stdout
