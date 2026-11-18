"""Synthetic attachment checks; never access Outlook or real mail."""

import os
import subprocess
from pathlib import Path

import pytest
from actions import ActionError
from pydantic import ValidationError

import outlook_actions as api
from office_bridge import desktop
from office_bridge.outlook.models import AttachmentDownload


def test_download_returns_typed_metadata_and_literal_reference(monkeypatch):
    calls = []

    def invoke(operation, arguments):
        calls.append((operation, arguments))
        return {
            "ref": {key: arguments[key] for key in ("entry_id", "store_id")},
            "items": [
                {
                    "index": 1,
                    "filename": "../invoice.pdf",
                    "size_bytes": 42,
                    "local_path": r"C:\private\01___invoice.pdf",
                    "sha256": "a" * 64,
                }
            ],
        }

    monkeypatch.setattr(desktop, "invoke", invoke)
    response = api.outlook_download_attachments(
        "entry'$(untrusted)", "store", conversation_id="conversation-1"
    )
    assert response.result.items[0].filename == "../invoice.pdf"
    assert response.result.items[0].sha256 == "a" * 64
    assert calls == [
        (
            "download_attachments",
            {
                "entry_id": "entry'$(untrusted)",
                "conversation_id": "conversation-1",
                "store_id": "store",
                "pdf_only": False,
            },
        )
    ]
    api.outlook_download_attachments("entry", "store", pdf_only=True)
    assert calls[-1][1]["pdf_only"] is True


def test_download_failure_is_reported_once(monkeypatch):
    calls = []

    def invoke(*args):
        calls.append(args)
        raise desktop.BridgeError("attachment_limit_exceeded")

    monkeypatch.setattr(desktop, "invoke", invoke)
    with pytest.raises(ActionError, match="attachment_limit_exceeded"):
        api.outlook_download_attachments("entry", "store")
    assert len(calls) == 1


def test_download_model_rejects_invalid_metadata():
    with pytest.raises(ValidationError):
        AttachmentDownload(index=26, filename="a", size_bytes=-1, local_path="", sha256="bad")


@pytest.mark.skipif(os.name != "nt", reason="Windows native host required")
def test_native_attachment_filenames_cannot_escape_cache(tmp_path):
    source = Path(__file__).parents[1] / "src/office_bridge/native/outlook/OutlookSession.cs"
    sdk = (
        Path(os.environ["LOCALAPPDATA"])
        / "OfficeActionServer/webview2-sdk/1.0.4314.35/Microsoft.Web.WebView2.Core.dll"
    )
    harness = tmp_path / "synthetic.ps1"
    harness.write_text(
        r"""
param([string]$Source,[string]$Sdk)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -Path $Sdk
Add-Type -Path $Source -ReferencedAssemblies @(
 [System.Windows.Forms.Form].Assembly.Location,[System.Drawing.Size].Assembly.Location,$Sdk,'System.Web.Extensions'
)
$method=[OutlookSession].GetMethod('SafeAttachmentName',[Reflection.BindingFlags]'Static,NonPublic')
foreach($name in @('../../evil.pdf','C:\temp\x:stream.pdf','CON','...','',"bad`nname.pdf",'a|b?c*')){
 $safe=[string]$method.Invoke($null,@($name,1))
 if($safe -notmatch '^01_' -or $safe -match '[\x00-\x1f<>:"/\\|?*]' -or $safe.EndsWith('.')){throw 'Unsafe filename'}
 if([IO.Path]::GetFileName($safe) -cne $safe){throw 'Path traversal'}
}
$long=[string]$method.Invoke($null,@((('x'*300)+'.pdf'),25))
if($long.Length -gt 123 -or -not $long.EndsWith('.pdf')){throw 'Filename bound'}
if($method.Invoke($null,@('same.pdf',1)) -ceq $method.Invoke($null,@('same.pdf',2))){throw 'Filename collision'}
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
            "-Source",
            str(source),
            "-Sdk",
            str(sdk),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stderr
