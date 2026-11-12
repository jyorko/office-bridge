"""Teams operations and durable send reservations, independent of action decorators."""

import hashlib
import json
import os
import sqlite3
from pathlib import Path

from office_bridge import desktop
from office_bridge.teams import ledger
from office_bridge.teams.models import GroupReceipt, WebChatHistory, WebSelfReceipt


class TeamsError(RuntimeError):
    """Bounded, user-facing operation failure."""


def read_chat(operation: str, chat: str, limit: int) -> WebChatHistory:
    try:
        result = desktop.invoke(operation, {"limit": limit})
        if result.get("state") != "read":
            raise ValueError("unverified")
        return WebChatHistory(
            chat=chat, scope="currently_loaded_history", messages=result["messages"]
        )
    except desktop.BridgeError as error:
        raise TeamsError(f"Could not safely read the specified Teams chat: {error.code}.") from None
    except (KeyError, ValueError):
        raise TeamsError("Could not safely read the specified Teams chat.") from None


def send_group(body: str, request_id: str) -> GroupReceipt:
    path = (
        Path(os.environ["LOCALAPPDATA"])
        / "OfficeActionServer"
        / "teams-web-sends"
        / "group.sqlite3"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    with sqlite3.connect(path, timeout=3) as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS sends (id TEXT PRIMARY KEY, digest TEXT NOT NULL, receipt TEXT)"
        )
        db.execute("BEGIN IMMEDIATE")
        old = db.execute("SELECT digest, receipt FROM sends WHERE id=?", (request_id,)).fetchone()
        if old:
            if old[0] != digest or not old[1]:
                raise TeamsError(
                    "Request already attempted or conflicts; do not retry automatically."
                )
            return GroupReceipt(**{**json.loads(old[1]), "status": "already_observed"})
        db.execute("INSERT INTO sends(id,digest,receipt) VALUES (?,?,NULL)", (request_id, digest))
        db.commit()
    try:
        result = desktop.invoke("teams_web_send_group", {"body": body, "request_id": request_id})
        if (
            result.get("state") != "observed"
            or not result.get("message_id")
            or not result.get("time")
        ):
            raise ValueError("unverified")
        receipt = GroupReceipt(
            status="observed",
            chat="QA-ASSIST-VERINT-DEPLOY",
            message_id=result["message_id"],
            time=result["time"],
        )
        with sqlite3.connect(path, timeout=3) as db:
            db.execute(
                "UPDATE sends SET receipt=? WHERE id=?", (receipt.model_dump_json(), request_id)
            )
        return receipt
    except Exception:
        raise TeamsError(
            "Send outcome unknown; inspect the chat before considering any retry."
        ) from None


def send_self(body: str, request_id: str) -> WebSelfReceipt:
    def submit():
        result = desktop.invoke("teams_web_send_self", {"body": body, "request_id": request_id})
        if result.get("state") != "observed":
            raise ValueError("unverified")
        return {
            "status": "observed",
            "message_id": result.get("message_id"),
            "recipient": ledger.RECIPIENT,
            "account_verified": True,
            "send_clicked": True,
        }

    try:
        result = ledger.send_once(request_id, body, submit)
        return WebSelfReceipt(
            status=result["status"], chat="Yorko, Joshua (You)", message_id=result["message_id"]
        )
    except (ledger.SendLedgerError, KeyError, ValueError):
        raise TeamsError(
            "Send outcome unknown; inspect the chat before considering any retry."
        ) from None
