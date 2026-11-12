"""Fixed, bounded JSON-file boundary to Windows PowerShell. No shell interpolation."""
import json
import os
from pathlib import Path
import subprocess
import tempfile

OPERATIONS = frozenset({"inbox_list", "get_message", "search_inbox", "create_reply_draft", "create_mail_draft", "send_draft", "archive", "list_chats", "read_chat", "send_self_message", "download_attachments", "teams_web_status", "teams_web_send_group", "teams_web_send_self", "teams_web_read_group", "teams_web_read_self"})
MUTATIONS = frozenset({"create_reply_draft", "create_mail_draft", "send_draft", "archive", "send_self_message", "download_attachments", "teams_web_send_group", "teams_web_send_self"})
MAX_BYTES = 1_048_576
SCRIPT = Path(__file__).parent / "powershell" / "office.ps1"


class BridgeError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def invoke(operation: str, arguments: dict) -> dict:
    if operation not in OPERATIONS:
        raise BridgeError("invalid_operation")
    if os.name != "nt":
        raise BridgeError("windows_required")
    if operation in {"teams_web_status", "teams_web_send_group", "teams_web_send_self", "teams_web_read_group", "teams_web_read_self"}:
        from teams_web_session import SessionError, invoke as invoke_session
        try:
            return invoke_session(operation, arguments, timeout=90)
        except SessionError as error:
            raise BridgeError("mutation_outcome_unknown" if operation in MUTATIONS else error.args[0]) from None
    executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    script = SCRIPT.with_name("attachments.ps1") if operation == "download_attachments" else SCRIPT
    with tempfile.TemporaryDirectory(prefix="office-action-") as directory:
        request = Path(directory) / "request.json"
        result = Path(directory) / "result.json"
        request.write_text(json.dumps({"version": 1, "operation": operation, "arguments": arguments}, ensure_ascii=False), encoding="utf-8")
        process = subprocess.Popen([str(executable), "-NoProfile", "-NonInteractive", "-STA", "-File", str(script), "-RequestFile", str(request), "-ResultFile", str(result)], shell=False, creationflags=subprocess.CREATE_NO_WINDOW, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            process.wait(timeout=45 if operation in MUTATIONS else 30)
        except subprocess.TimeoutExpired:
            # Kill only this subprocess tree. Never terminate Office applications.
            try:
                subprocess.run([str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/taskkill.exe"), "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=False)
            except (OSError, subprocess.TimeoutExpired):
                pass
            finally:
                try:
                    process.kill()
                    process.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            raise BridgeError("mutation_outcome_unknown" if operation in MUTATIONS else "timeout") from None
        if not result.exists():
            raise BridgeError("mutation_outcome_unknown" if operation in MUTATIONS else "bridge_failed")
        if result.stat().st_size > MAX_BYTES:
            raise BridgeError("output_overflow")
        try:
            envelope = json.loads(result.read_text(encoding="utf-8-sig"))
            if not isinstance(envelope, dict) or envelope.get("version") != 1 or type(envelope.get("ok")) is not bool:
                raise ValueError("protocol")
            if not envelope.get("ok"):
                code = envelope.get("error", "bridge_failed")
                raise BridgeError(code if isinstance(code, str) and len(code) < 80 else "bridge_failed")
            if not isinstance(envelope.get("result"), dict):
                raise ValueError("result")
            return envelope["result"]
        except (ValueError, UnicodeError):
            raise BridgeError("invalid_bridge_result") from None
