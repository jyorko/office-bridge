"""Exercise the production C# send loop with an offline browser boundary."""

import os
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(os.name != "nt", reason="Windows C# compiler harness")
def test_native_plain_and_markdown_send_return_observed_receipts(tmp_path):
    source = (
        Path(__file__).parents[1] / "src/office_bridge/native/teams/TeamsSession.cs"
    ).read_text(encoding="utf-8")
    start = source.index("    async Task<Dictionary<string, object>> Send(")
    end = source.index("    async Task<Dictionary<string, object>> RunChat(", start)
    send = source[start:end]
    plan = source[source.index("    sealed class MentionPlanItem") : start]
    context_start = source.index("    async Task<CrossChatContext> BeginChatContext(")
    context_end = source.index("    async Task<bool> PrimaryUnchanged(", context_start)
    context_selection = source[context_start:context_end]
    harness = r"""
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
public class NativeSendFixture {
    static readonly JavaScriptSerializer Json = new JavaScriptSerializer();
    string groupScript = "offline";
    string profile;
    string currentAccountName = "Fixture Author";
    string expectedPhase;
    int clicks, pastes, inserts;
    class Core {
        public NativeSendFixture Owner;
        public Task<string> CallDevToolsProtocolMethodAsync(string method, string arguments) {
            if (method == "Input.insertText") {
                Owner.inserts++;
                Owner.expectedPhase = "send";
            }
            return Task.FromResult("{}");
        }
    }
    class CrossChatContext {
        public Core Core;
        public bool ProtectPrimary = false;
    }
    // PRODUCTION_MENTION_PLAN
    static bool ValidChatId(string id) { return id == "48:notes"; }
    bool ScriptsUnchanged() { return true; }
    Task<bool> PrimaryUnchanged(CrossChatContext context) { return Task.FromResult(true); }
    Task VerifyCrossChat(CrossChatContext context, string author) { return Task.FromResult(0); }
    Task OpenExactChatOnAuxiliary(Core core, string id, string title) { return Task.FromResult(0); }
    Task<CrossChatContext> BeginCrossChat(bool auxiliary = false) {
        expectedPhase = auxiliary ? "locate" : "navigate";
        return Task.FromResult(new CrossChatContext {
            Core = new Core { Owner = this }, ProtectPrimary = auxiliary
        });
    }
    Task<Dictionary<string, object>> ExecuteChat(Core core, Dictionary<string, object> request) {
        if ((string)request["phase"] != "list")
            throw new Exception("Unexpected chat-list phase");
        return Task.FromResult(new Dictionary<string, object> {
            {"items", new object[0]}
        });
    }
    Task InsertTrackedSendText(Core core, string chatId, string title, string requestId,
        string authorName, string navigationId, string text) {
        throw new Exception("Unexpected mention path");
    }
    Task InsertTrackedMentionQuery(Core core, string chatId, string title, string requestId,
        string authorName, string navigationId, string text) {
        throw new Exception("Unexpected mention path");
    }
    Task<Dictionary<string, object>> ClearOwnedSendDraft(Core core, Dictionary<string, object> request) {
        throw new Exception("Unexpected mention cleanup");
    }
    Task<Dictionary<string, object>> RunChat(Core core, Dictionary<string, object> request) {
        string phase = (string)request["phase"];
        if (phase == "validate_payload")
            return Task.FromResult(new Dictionary<string, object> { {"state", "payload_valid"} });
        if (phase != expectedPhase)
            throw new Exception("Unexpected phase: " + phase + ", expected " + expectedPhase);
        string state;
        if (phase == "locate") { state = "ready"; expectedPhase = "prepare"; }
        else if (phase == "prepare") {
            state = "prepared";
            expectedPhase = (string)request["bodyFormat"] == "markdown" ? "paste" : "insert";
        } else if (phase == "paste") {
            pastes++;
            state = "pasted";
            expectedPhase = "send";
        } else if (phase == "send") {
            clicks++;
            state = "clicked";
            expectedPhase = "observe";
        } else if (phase == "observe") {
            return Task.FromResult(new Dictionary<string, object> {
                {"state", "observed"}, {"chat_id", "48:notes"}, {"title", "Fixture"},
                {"message_id", "message-body-1700000000000"}, {"time", "fixture"}
            });
        } else throw new Exception("Unexpected browser phase");
        return Task.FromResult(new Dictionary<string, object> { {"state", state} });
    }
"""
    harness = harness.replace("    // PRODUCTION_MENTION_PLAN", plan)
    harness += context_selection
    harness += send
    harness += r"""
    public static void Check(string root) {
        var mention = new Dictionary<string, object> {
            {"key", "person"}, {"display_label", "Example Person"},
            {"recipient_email", "person@example.invalid"}
        };
        var plan = new object[] {mention};
        if (ValidateMentionPlan(plan, "{{mention:person}} test", "plain").Count != 1)
            throw new Exception("Valid mention placeholder rejected");
        foreach (string invalid in new[] {
            "{{mention:person}} {{mention:person}}", "{{mention:other}}",
            "{{mention:person}} {{mention:broken", "plain text" }) {
            bool rejected = false;
            try { ValidateMentionPlan(plan, invalid, "plain"); }
            catch (InvalidDataException) { rejected = true; }
            if (!rejected) throw new Exception("Invalid mention placeholder accepted");
        }
        foreach (string format in new [] { "plain", "markdown" }) {
            var fixture = new NativeSendFixture { profile = Path.Combine(root, "profile") };
            var result = fixture.Send(new Dictionary<string, object> {
                {"chat", new Dictionary<string, object> {
                    {"chat_id", "48:notes"}, {"title", "Fixture"} }},
                {"body", "Fixture body"}, {"body_format", format}, {"request_id", format}
            }).GetAwaiter().GetResult();
            if ((string)result["state"] != "observed" || fixture.clicks != 1 ||
                fixture.pastes != (format == "markdown" ? 1 : 0) ||
                fixture.inserts != (format == "plain" ? 1 : 0))
                throw new Exception("Send did not return one verified receipt");
            string digest = BitConverter.ToString(
                SHA256.Create().ComputeHash(Encoding.UTF8.GetBytes(format)))
                .Replace("-", "").ToLowerInvariant();
            if (!File.Exists(Path.Combine(root, "teams-web-sends", "claims", digest + ".json.receipt")))
                throw new Exception("Native receipt was not persisted");
        }
    }
}
"""
    cs_path = tmp_path / "native-send.cs"
    cs_path.write_text(harness, encoding="utf-8")
    ps_path = tmp_path / "native-send.ps1"
    ps_path.write_text(
        """
param([string]$SourceFile, [string]$FixtureRoot)
$ErrorActionPreference = 'Stop'
Add-Type -Path $SourceFile -ReferencedAssemblies 'System.Web.Extensions','System.Core'
[NativeSendFixture]::Check($FixtureRoot)
Write-Output 'Native plain and Markdown receipts verified'
""",
        encoding="utf-8",
    )
    process = subprocess.run(
        [
            str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(ps_path),
            "-SourceFile",
            str(cs_path),
            "-FixtureRoot",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    assert "Native plain and Markdown receipts verified" in process.stdout
