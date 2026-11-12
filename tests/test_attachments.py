"""Synthetic attachment checks; never access Outlook or real mail."""
from pathlib import Path
import subprocess

import pytest
from actions import ActionError
from pydantic import ValidationError

import attachment_actions as api


def test_download_returns_typed_metadata_and_literal_reference(monkeypatch):
    calls = []

    def invoke(operation, arguments):
        calls.append((operation, arguments))
        return {"ref": {key: arguments[key] for key in ("entry_id", "store_id")}, "items": [{"index": 1, "filename": "../invoice.pdf", "size_bytes": 42,
                 "local_path": r"C:\private\01___invoice.pdf", "sha256": "a" * 64}]}

    monkeypatch.setattr(api.desktop_bridge, "invoke", invoke)
    response = api.outlook_download_attachments("entry'$(untrusted)", "store")
    assert response.result.items[0].filename == "../invoice.pdf"
    assert response.result.items[0].sha256 == "a" * 64
    assert calls == [("download_attachments", {"entry_id": "entry'$(untrusted)", "store_id": "store", "pdf_only": False})]
    api.outlook_download_attachments("entry", "store", pdf_only=True)
    assert calls[-1][1]["pdf_only"] is True


def test_download_failure_is_reported_once(monkeypatch):
    calls = []

    def invoke(*args):
        calls.append(args)
        raise api.desktop_bridge.BridgeError("attachment_limit_exceeded")

    monkeypatch.setattr(api.desktop_bridge, "invoke", invoke)
    with pytest.raises(ActionError, match="attachment_limit_exceeded"):
        api.outlook_download_attachments("entry", "store")
    assert len(calls) == 1


def test_download_model_rejects_invalid_metadata():
    with pytest.raises(ValidationError):
        api.AttachmentDownload(index=26, filename="a", size_bytes=-1, local_path="", sha256="bad")


def test_powershell_filename_and_limit_boundaries(tmp_path):
    source = Path(__file__).parents[1] / "powershell" / "attachments.ps1"
    harness = tmp_path / "synthetic.ps1"
    harness.write_text(r'''
param([string]$SourceFile)
$ErrorActionPreference='Stop'
$tokens=$null;$errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile($SourceFile,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'PowerShell syntax errors'}
$ast.FindAll({param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst]},$false) |
 ForEach-Object {. ([scriptblock]::Create($_.Extent.Text))}
function Must-Fail([scriptblock]$code,[string]$expected){
 $caught=$false
 try{& $code}catch{if($_.Exception.Message -cne $expected){throw};$caught=$true}
 if(-not $caught){throw 'Expected failure'}
}
foreach($name in @('../../evil.pdf','C:\\temp\\x:stream.pdf','CON','...','',"bad`nname.pdf",'a|b?c*')){
 $safe=Safe-Name $name 1
 if($safe -notmatch '^01_' -or $safe -match '[\x00-\x1f<>:"/\\|?*]' -or $safe.EndsWith('.')){throw 'Unsafe filename'}
 if([IO.Path]::GetFileName($safe) -cne $safe){throw 'Path traversal'}
}
$long=Safe-Name (('x'*300)+'.pdf') 25
if($long.Length -gt 123 -or -not $long.EndsWith('.pdf')){throw 'Filename bound'}
if((Safe-Name 'same.pdf' 1) -ceq (Safe-Name 'same.pdf' 2)){throw 'Duplicate filename collision'}
Check-Limits 25 52428800
Check-Limits 0 0
Must-Fail {Check-Limits 26 0} 'attachment_limit_exceeded'
Must-Fail {Check-Limits 1 52428801} 'attachment_limit_exceeded'
Must-Fail {Check-Limits 1 -1} 'attachment_limit_exceeded'
Must-Fail {Safe-Name 'x' 0} 'invalid_input'
''', encoding="utf-8")
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-File", str(harness),
                             "-SourceFile", str(source)], capture_output=True, text=True,
                            timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0, result.stderr
