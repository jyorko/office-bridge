"""Compile the Outlook host and exercise its response-identity reducer with Mono.

This is a compile and logic gate only. It does not run WebView2, sign in, or
prove anything about a live mailbox.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/office_bridge/native/outlook/OutlookSession.cs"
SDK = (
    Path(os.environ.get("OFFICE_BRIDGE_TEST_WEBVIEW2_SDK", "")) / "Microsoft.Web.WebView2.Core.dll"
)
HARNESS = r"""
using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Web.Script.Serialization;

static class Harness
{
    static void Check(bool value, string name) { if (!value) throw new Exception(name); }

    static int Main()
    {
        var method = typeof(OutlookSession).GetMethod(
            "ObservedItems", BindingFlags.Static | BindingFlags.NonPublic);
        var json = new JavaScriptSerializer();
        Func<string, Dictionary<string, List<Dictionary<string, object>>>> run = text =>
            (Dictionary<string, List<Dictionary<string, object>>>)method.Invoke(
                null, new object[] { json.DeserializeObject(text) });

        var found = run(@"{""Body"":{""ResponseMessages"":{""Items"":[{""Conversation"":{
          ""ConversationNodes"":[{""Items"":[
            {""ItemId"":{""Id"":""item-a"",""ChangeKey"":""ck-a""},
             ""ConversationId"":{""Id"":""conv-a""},""IsDraft"":false,""HasAttachments"":true,
             ""ToRecipients"":[{""Mailbox"":{""Name"":""A Person"",""EmailAddress"":""A@Example.com""}}],
             ""CcRecipients"":[{""Name"":""B"",""EmailAddress"":""b@example.com""}],
             ""UniqueBody"":{""Value"":""secret body""}},
            {""ItemId"":{""Id"":""item-a"",""ChangeKey"":""ck-a""},
             ""ConversationId"":{""Id"":""conv-a""},""IsDraft"":false,""HasAttachments"":true,
             ""ToRecipients"":[{""Mailbox"":{""Name"":""A Person"",""EmailAddress"":""A@Example.com""}}],
             ""CcRecipients"":[{""Name"":""B"",""EmailAddress"":""b@example.com""}]},
            {""ItemId"":{""Id"":""draft-a"",""ChangeKey"":""ck-d""},
             ""ConversationId"":{""Id"":""conv-a""},""IsDraft"":true,""HasAttachments"":false,
             ""ToRecipients"":[]}]}]}},
          {""ItemId"":{""Id"":""item-b"",""ChangeKey"":""ck-b""},
           ""ConversationId"":{""Id"":""conv-b""},
           ""ToRecipients"":[{""Mailbox"":{""Name"":""X"",""EmailAddress"":""/o=legacy""}}]},
          {""ItemId"":{""Id"":""no-key""},""ConversationId"":{""Id"":""conv-c""}},
          {""ItemId"":{""Id"":""bad\u0001id"",""ChangeKey"":""k""},""ConversationId"":{""Id"":""conv-c""}}
        ]}}}");
        Check(found.Count == 2, "conversation count");
        var a = found["conv-a"];
        Check(a.Count == 2, "duplicate item collapsed");
        var item = a.Single(r => (string)r["entry_id"] == "item-a");
        Check((string)item["change_key"] == "ck-a", "change key");
        Check((bool)item["complete"] && (bool)item["has_attachments"] && !(bool)item["is_draft"], "flags");
        var recipients = (List<Dictionary<string, object>>)item["recipients"];
        Check(recipients.Count == 2, "recipient count");
        Check((string)recipients[0]["smtp_address"] == "a@example.com" &&
              (int)recipients[0]["recipient_type"] == 1, "to recipient");
        Check((string)recipients[1]["smtp_address"] == "b@example.com" &&
              (int)recipients[1]["recipient_type"] == 2, "cc recipient");
        Check(item.Keys.All(k => k != "body_text") &&
              !item.Values.Any(v => Convert.ToString(v).Contains("secret")), "no body retained");
        Check((bool)a.Single(r => (string)r["entry_id"] == "draft-a")["is_draft"], "draft flag");
        var b = found["conv-b"].Single();
        Check(!(bool)b["complete"], "unresolved recipient or missing flags are incomplete");
        Check(((List<Dictionary<string, object>>)b["recipients"]).Count == 0, "legacy address dropped");
        Check(!found.ContainsKey("conv-c"), "items without a version or with control characters");
        Check(run("[1,2,{\"x\":null}]").Count == 0, "unrelated json");
        Console.WriteLine("ok");
        return 0;
    }
}
"""


@pytest.mark.skipif(
    not shutil.which("mcs") or not shutil.which("mono") or not SDK.is_file(),
    reason="Mono and OFFICE_BRIDGE_TEST_WEBVIEW2_SDK are required",
)
def test_host_compiles_and_reduces_responses_to_identity_records(tmp_path):
    shutil.copy(SDK, tmp_path / SDK.name)
    harness = tmp_path / "Harness.cs"
    harness.write_text(HARNESS, encoding="utf-8")
    references = [
        "-r:System.Windows.Forms.dll",
        "-r:System.Drawing.dll",
        "-r:System.Web.Extensions.dll",
        f"-r:{tmp_path / SDK.name}",
    ]
    library = tmp_path / "OutlookSession.dll"
    built = subprocess.run(
        ["mcs", "-langversion:5", "-target:library", "-nowarn:1998", f"-out:{library}"]
        + references
        + [str(SOURCE)],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    program = tmp_path / "Harness.exe"
    built = subprocess.run(
        ["mcs", "-langversion:5", f"-out:{program}", f"-r:{library}"] + references + [str(harness)],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    ran = subprocess.run(["mono", str(program)], capture_output=True, text=True, timeout=60)
    assert ran.returncode == 0 and ran.stdout.strip() == "ok", ran.stdout + ran.stderr
