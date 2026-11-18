"""Bounded clients for persistent Office hosts and legacy desktop Teams reads."""

import json
import os
import subprocess
import tempfile
from pathlib import Path

from actions import ActionError, Response
from pydantic import BaseModel

from office_bridge.models import Model
from office_bridge.paths import NATIVE

OPERATIONS = frozenset(
    {
        "inbox_list",
        "get_message",
        "search_inbox",
        "create_reply_draft",
        "create_mail_draft",
        "send_draft",
        "archive",
        "list_chats",
        "read_chat",
        "download_attachments",
        "teams_web_status",
        "teams_web_list_chats",
        "teams_web_find_chats",
        "teams_web_read_chat",
        "teams_web_send_chat",
        "teams_web_inspect_message",
        "teams_web_inspect_mention_evidence",
        "teams_web_edit_message",
        "teams_web_upload_readiness",
        "teams_web_upload_files",
        "teams_web_upload_status",
        "teams_web_message_link",
        "teams_web_message_reactions",
        "teams_web_react_message",
        "teams_web_set_reaction",
    }
)
MUTATIONS = frozenset(
    {
        "create_reply_draft",
        "create_mail_draft",
        "send_draft",
        "archive",
        "download_attachments",
        "teams_web_send_chat",
        "teams_web_edit_message",
        "teams_web_upload_files",
        "teams_web_react_message",
        "teams_web_set_reaction",
    }
)
OUTLOOK_OPERATIONS = {
    "inbox_list": "outlook_inbox_list",
    "get_message": "outlook_get_message",
    "search_inbox": "outlook_search_inbox",
    "create_reply_draft": "outlook_create_reply_draft",
    "create_mail_draft": "outlook_create_mail_draft",
    "send_draft": "outlook_send_draft",
    "archive": "outlook_archive",
    "download_attachments": "outlook_download_attachments",
}
OUTLOOK_SUPPORTED = frozenset(OUTLOOK_OPERATIONS)
OUTLOOK_UNCERTAIN_MUTATIONS = frozenset(
    {"create_reply_draft", "create_mail_draft", "send_draft", "archive"}
)
MAX_BYTES = 1_048_576
SCRIPT = NATIVE / "office.ps1"


class BridgeError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def invoke(operation: str, arguments: dict) -> dict:
    if operation not in OPERATIONS:
        raise BridgeError("invalid_operation")
    if os.name != "nt":
        raise BridgeError("windows_required")
    if operation in OUTLOOK_OPERATIONS:
        if operation not in OUTLOOK_SUPPORTED:
            raise BridgeError("outlook_operation_unavailable")
        from office_bridge.outlook.session import SessionError
        from office_bridge.outlook.session import invoke as invoke_outlook

        try:
            timeout = (
                300
                if operation == "download_attachments"
                else 35
                if operation == "get_message"
                else 90
            )
            return invoke_outlook(OUTLOOK_OPERATIONS[operation], arguments, timeout=timeout)
        except SessionError as error:
            code = error.args[0] if error.args else "host_operation_failed"
            if operation in OUTLOOK_UNCERTAIN_MUTATIONS and code in {
                "session_unavailable",
                "session_write_failed",
                "session_disconnected",
                "session_timeout",
                "invalid_response",
                "host_operation_failed",
            }:
                code = "mutation_outcome_unknown"
            raise BridgeError(code) from None
    if operation in {
        "teams_web_status",
        "teams_web_list_chats",
        "teams_web_find_chats",
        "teams_web_read_chat",
        "teams_web_send_chat",
        "teams_web_inspect_message",
        "teams_web_inspect_mention_evidence",
        "teams_web_edit_message",
        "teams_web_upload_readiness",
        "teams_web_upload_files",
        "teams_web_upload_status",
        "teams_web_message_link",
        "teams_web_message_reactions",
        "teams_web_react_message",
        "teams_web_set_reaction",
    }:
        from office_bridge.teams.session import SessionError
        from office_bridge.teams.session import invoke as invoke_session

        try:
            timeout = 300 if operation == "teams_web_upload_files" else 90
            return invoke_session(operation, arguments, timeout=timeout)
        except SessionError as error:
            raise BridgeError(
                "mutation_outcome_unknown" if operation in MUTATIONS else error.args[0]
            ) from None
    executable = (
        Path(os.environ.get("SystemRoot", r"C:\Windows"))
        / "System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    with tempfile.TemporaryDirectory(prefix="office-action-") as directory:
        request = Path(directory) / "request.json"
        result = Path(directory) / "result.json"
        request.write_text(
            json.dumps(
                {"version": 1, "operation": operation, "arguments": arguments}, ensure_ascii=False
            ),
            encoding="utf-8",
        )
        process = subprocess.Popen(
            [
                str(executable),
                "-NoProfile",
                "-NonInteractive",
                "-STA",
                "-File",
                str(SCRIPT),
                "-RequestFile",
                str(request),
                "-ResultFile",
                str(result),
            ],
            shell=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            process.wait(timeout=45 if operation in MUTATIONS else 30)
        except subprocess.TimeoutExpired:
            # Kill only this subprocess tree. Never terminate Office applications.
            try:
                subprocess.run(
                    [
                        str(
                            Path(os.environ.get("SystemRoot", r"C:\Windows"))
                            / "System32/taskkill.exe"
                        ),
                        "/PID",
                        str(process.pid),
                        "/T",
                        "/F",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
            finally:
                try:
                    process.kill()
                    process.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            raise BridgeError(
                "mutation_outcome_unknown" if operation in MUTATIONS else "timeout"
            ) from None
        if not result.exists():
            raise BridgeError(
                "mutation_outcome_unknown" if operation in MUTATIONS else "bridge_failed"
            )
        if result.stat().st_size > MAX_BYTES:
            raise BridgeError("output_overflow")
        try:
            envelope = json.loads(result.read_text(encoding="utf-8-sig"))
            if (
                not isinstance(envelope, dict)
                or envelope.get("version") != 1
                or type(envelope.get("ok")) is not bool
            ):
                raise ValueError("protocol")
            if not envelope.get("ok"):
                code = envelope.get("error", "bridge_failed")
                raise BridgeError(
                    code if isinstance(code, str) and len(code) < 80 else "bridge_failed"
                )
            if not isinstance(envelope.get("result"), dict):
                raise ValueError("result")
            return envelope["result"]
        except (ValueError, UnicodeError):
            raise BridgeError("invalid_bridge_result") from None


def run(operation: str, model: type[Model], **arguments):
    arguments = {
        key: value.model_dump(mode="json") if isinstance(value, BaseModel) else value
        for key, value in arguments.items()
    }
    try:
        return Response(result=model.model_validate(invoke(operation, arguments)))
    except BridgeError as error:
        raise ActionError(
            f"Desktop operation failed: {error.code}. Do not automatically retry mutations."
        ) from None
