"""Run only extracted pure helper functions against synthetic objects, never Office."""

import subprocess
from pathlib import Path


def test_hidden_process_with_duplicate_environment_names(tmp_path):
    source = (
        Path(__file__).parents[1] / "src" / "office_bridge" / "native" / "process-environment.ps1"
    )
    harness = tmp_path / "environment.ps1"
    output = tmp_path / "output.txt"
    harness.write_text(
        r"""
param([string]$SourceFile,[string]$OutputFile)
$ErrorActionPreference='Stop'
Add-Type -TypeDefinition @'
using System.Runtime.InteropServices;
public static class EnvironmentFixture {
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool SetEnvironmentStringsW(string block);
}
'@
$before = [Environment]::GetEnvironmentVariable('Path', 'Process')
$variables = [Environment]::GetEnvironmentVariables('Process')
$entries = @($variables.Keys | Where-Object { $_ -ine 'Path' } | ForEach-Object { "$_=$($variables[$_])" })
$entries += "Path=$before", "PATH=$before", 'OFFICE_BRIDGE_ENV_TEST=preserved'
if (-not [EnvironmentFixture]::SetEnvironmentStringsW(($entries -join "`0") + "`0`0")) {
    throw 'Could not create raw duplicate environment block'
}
$names = @([Environment]::GetEnvironmentVariables('Process').Keys | Where-Object { $_ -ieq 'Path' })
if ($names.Count -ne 2) { throw 'Duplicate environment fixture was not inherited' }
. $SourceFile
Repair-ProcessEnvironment
Repair-ProcessEnvironment
$names = @([Environment]::GetEnvironmentVariables('Process').Keys | Where-Object { $_ -ieq 'Path' })
if ($names.Count -ne 1) { throw 'Duplicate environment names remain' }
if ([Environment]::GetEnvironmentVariable('Path', 'Process') -cne $before) { throw 'Effective Path changed' }
$child = Start-Process -FilePath "$env:SystemRoot\System32\cmd.exe" -ArgumentList '/d /c echo %OFFICE_BRIDGE_ENV_TEST%' -WindowStyle Hidden -Wait -PassThru -RedirectStandardOutput $OutputFile
if ($child.ExitCode -ne 0) { throw 'Hidden child launch failed' }
if ((Get-Content -LiteralPath $OutputFile -Raw).Trim() -cne 'preserved') { throw 'Unrelated environment changed' }
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
            "-OutputFile",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr


def test_desktop_teams_navigation_guards(tmp_path):
    source = Path(__file__).parents[1] / "src" / "office_bridge" / "native" / "office.ps1"
    harness = tmp_path / "synthetic.ps1"
    harness.write_text(
        r"""
param([string]$SourceFile)
$ErrorActionPreference='Stop'
$tokens=$null;$errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile($SourceFile,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'PowerShell syntax errors'}
# Extract function definitions only. The dispatcher and UI calls never execute.
$functions=$ast.FindAll({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst]},$false)
$functions | ForEach-Object {. ([scriptblock]::Create($_.Extent.Text))}
$utf8=New-Object System.Text.UTF8Encoding($false)
function Assert-Failure([scriptblock]$code,[string]$expectedCode){
 $caught=$false
 try{& $code}catch{if($_.Exception.Message -cne $expectedCode){throw};$caught=$true}
 if(-not $caught){throw "Expected $expectedCode"}
}
if((Chat-Name 'Chat Briggs, Gary Out of office Last message text') -cne 'Briggs, Gary'){throw 'DM normalization failed'}
if((Chat-Name 'Group chat Design Review Has pinned messages Last message text') -cne 'Design Review'){throw 'Group normalization failed'}
if($null -ne (Chat-Name 'Favorites')){throw 'Category accepted as chat'}
if((Chat-Name 'Chat Person In a meeting Last message text') -cne 'Person'){throw 'Meeting presence normalization failed'}
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
