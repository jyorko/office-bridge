"""Teams operations and durable send reservations, independent of action decorators."""

import hashlib
import json
import os
import sqlite3
from pathlib import Path

from office_bridge import desktop
from office_bridge.teams import ledger
from office_bridge.teams.models import (
    GroupReceipt,
    WebChatHistory,
    WebChatMessage,
    WebChatRef,
    WebChatSearchResult,
)


class TeamsError(RuntimeError):
    """Bounded, user-facing operation failure."""


def list_chats(name: str | None, limit: int) -> WebChatSearchResult:
    try:
        result = desktop.invoke("teams_web_list_chats", {"name": name, "limit": limit})
        if result.get("state") != "listed":
            raise ValueError("unverified")
        items = [WebChatRef.model_validate(item) for item in result["items"]]
        if name is not None and any(item.title != name for item in items):
            raise ValueError("inexact match")
        return WebChatSearchResult(
            items=items,
            scope="currently_loaded_chat_list",
            truncated=result["truncated"],
        )
    except desktop.BridgeError as error:
        raise TeamsError(f"Could not safely list Teams chats: {error.code}.") from None
    except (KeyError, ValueError):
        raise TeamsError("Could not safely list verified Teams chats.") from None


def read_chat(chat: WebChatRef, limit: int) -> WebChatHistory:
    try:
        result = desktop.invoke(
            "teams_web_read_chat",
            {"chat": chat.model_dump(mode="json"), "limit": limit},
        )
        if result.get("state") != "read":
            raise ValueError("unverified")
        if result.get("chat_id") != chat.chat_id or result.get("title") != chat.title:
            raise ValueError("chat identity mismatch")
        return WebChatHistory(
            chat=chat,
            scope="currently_loaded_history",
            messages=[WebChatMessage.model_validate(message) for message in result["messages"]],
        )
    except desktop.BridgeError as error:
        raise TeamsError(f"Could not safely read the specified Teams chat: {error.code}.") from None
    except (KeyError, ValueError):
        raise TeamsError("Could not safely read the specified Teams chat.") from None


def send_chat(chat: WebChatRef, body: str, request_id: str) -> GroupReceipt:
    try:
        if ledger.has_request_id(request_id):
            raise TeamsError(
                "Request ID exists in the retired self-send ledger; do not retry or reuse it."
            )
    except ledger.SendLedgerError:
        raise TeamsError("Could not safely check prior send history.") from None
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
            try:
                saved = json.loads(old[1]) if old[1] else {}
                old_chat = saved.get("chat")
                if isinstance(old_chat, dict):
                    old_chat = WebChatRef.model_validate(old_chat)
            except (TypeError, ValueError):
                old_chat = None
            if old[0] != digest or old_chat != chat:
                raise TeamsError(
                    "Request already attempted or conflicts; do not retry automatically."
                )
            return GroupReceipt(**{**json.loads(old[1]), "status": "already_observed"})
        db.execute("INSERT INTO sends(id,digest,receipt) VALUES (?,?,NULL)", (request_id, digest))
        db.commit()
    try:
        result = desktop.invoke(
            "teams_web_send_chat",
            {"chat": chat.model_dump(mode="json"), "body": body, "request_id": request_id},
        )
        if (
            result.get("state") != "observed"
            or not result.get("message_id")
            or not result.get("time")
            or result.get("chat_id") != chat.chat_id
            or result.get("title") != chat.title
        ):
            raise ValueError("unverified")
        receipt = GroupReceipt(
            status="observed",
            chat=chat,
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
