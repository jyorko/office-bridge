"""Dispatch one bound viewer click to the two published Outlook Runtime actions."""

import hmac
import json
import os
import sys
from pathlib import Path

import requests

from office_bridge.presentation import Presentation, ReadMail, ReplyToMail, can_read, can_reply
from office_bridge.runtime.endpoint import server_record


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
        response = session.post(url, json=args, timeout=(3, 65), allow_redirects=False)
        if response.status_code != 200:
            raise RuntimeError("Office Bridge could not confirm the action.")
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
    except Exception:
        # Exceptions can include paths, server URLs, request content or identifiers.
        reply = {
            "isError": True,
            "content": [{"type": "text", "text": "Office action unconfirmed."}],
        }
    sys.stdout.buffer.write(json.dumps(reply).encode("ascii"))
