"""Dispatch one bound viewer click to the two published Outlook Runtime actions."""

import hmac
import json
import os
import re
import sys
from pathlib import Path

import requests

from office_bridge.presentation import Presentation, ReadMail, ReplyToMail, can_read, can_reply
from office_bridge.runtime.endpoint import server_record

FAILURES = {
    "draft_exists": "Outlook has an open draft. Close or save it before reading another email.",
    "message_not_found": "This email is no longer in the loaded inbox. Refresh the inbox and select it again.",
    "message_identity_unavailable": "Outlook has not supplied a verified identity for this email. Try Read email again.",
    "message_identity_ambiguous": "Outlook opened more than one matching message. Select one email and retry the read.",
    "stale_message": "This email changed. Read it again before creating a reply draft.",
    "version_incomplete": "Outlook has not supplied a complete message version. Read the email again.",
    "mutation_outcome_unknown": "Outlook did not confirm the change. Inspect Drafts before trying another write.",
    "not_ready": "Outlook is still loading the selected email. Try Read email again.",
    "session_timeout": "Outlook did not finish this operation within its time limit.",
}


class ActionFailure(RuntimeError):
    """A static, safe explanation suitable for the reading window."""


def failure_message(value: object) -> str:
    text = json.dumps(value)[:20000]
    for code, explanation in FAILURES.items():
        if re.search(rf"(?<![a-z_]){code}(?![a-z_])", text, re.I):
            return f"Office action failed. {explanation}"
    return "Office action failed. The host did not confirm the operation."


def dispatch(click: dict) -> dict:
    display = Presentation.model_validate(click["display"])
    proof = display.proof()
    if not proof or not hmac.compare_digest(proof, display.window_proof or ""):
        raise ValueError("This view has no verified mail controls.")
    record = server_record(Path(os.environ["OFFICE_CANVAS_ROOT"]))
    if record["instance"] != click["instance"]:
        raise ValueError("The Office Bridge server changed. Open a fresh mail view.")
    name, args = click["name"], click["arguments"]
    binding = display.mail
    if name == "outlook_get_message" and isinstance(binding, ReadMail):
        if not any(
            row and can_read(row) and args == {"message": row.model_dump(mode="json")}
            for row in binding.rows
        ):
            raise ValueError("This message is outside the displayed inbox.")
        operation = "outlook-get-message"
    elif name == "outlook_create_reply_draft" and isinstance(binding, ReplyToMail):
        if (
            not can_reply(binding.source, binding.version)
            or set(args) != {"source", "expected_source", "reply_all", "body_text"}
            or args["source"] != binding.source.model_dump(mode="json")
            or args["expected_source"] != binding.version.model_dump(mode="json")
            or type(args["reply_all"]) is not bool
            or not isinstance(args["body_text"], str)
            or not args["body_text"].strip()
            or len(args["body_text"]) > 20000
        ):
            raise ValueError("The reply does not match the opened message.")
        operation = "outlook-create-reply-draft"
    else:
        raise ValueError("This action is unavailable in this view.")
    url = record["mcp_url"].removesuffix("/mcp") + f"/api/actions/desktop-office/{operation}/run"
    with requests.Session() as session:
        session.trust_env = False
        response = session.post(
            url,
            json=args,
            timeout=(3, 40 if operation == "outlook-get-message" else 65),
            allow_redirects=False,
        )
        if response.status_code != 200:
            try:
                explanation = failure_message(response.json())
            except ValueError:
                explanation = failure_message(None)
            raise ActionFailure(explanation)
        value = response.json()
    if operation == "outlook-get-message" and not value.get("error"):
        actual = (value.get("result") or {}).get("ref") or {}
        requested = args["message"]
        if not actual.get("entry_id") or any(
            actual.get(key) != requested[key]
            for key in ("store_id", "entry_id", "conversation_id")
            if requested.get(key)
        ):
            raise ValueError("Outlook did not resolve the selected message.")
    return value


if __name__ == "__main__":
    try:
        result = dispatch(json.load(sys.stdin.buffer))
        reply = {"structuredContent": result}
    except Exception as error:
        # Exceptions can include paths, server URLs, request content or identifiers.
        reply = {
            "isError": True,
            "content": [
                {
                    "type": "text",
                    "text": str(error)
                    if isinstance(error, ActionFailure)
                    else "Office action failed. The host did not confirm the operation.",
                }
            ],
        }
    sys.stdout.buffer.write(json.dumps(reply).encode("ascii"))
