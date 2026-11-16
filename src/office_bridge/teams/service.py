"""Teams operations and durable send reservations, independent of action decorators."""

import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path

from office_bridge import desktop
from office_bridge.teams import ledger
from office_bridge.teams.formatting import BodyFormatError, PreparedBody, format_body
from office_bridge.teams.models import (
    EditReceipt,
    GroupReceipt,
    WebChatHistory,
    WebChatMessage,
    WebChatRef,
    WebChatSearchResult,
)


class TeamsError(RuntimeError):
    """Bounded, user-facing operation failure."""


_MESSAGE_ID = re.compile(r"^message-body-[0-9]{13}$")


def _rich_identity(source: str, body_format: str, prepared: PreparedBody) -> str:
    if body_format == "plain":
        return source
    return json.dumps(
        {
            "body_format": body_format,
            "source": source,
            "body": prepared.body,
            "html": prepared.html,
            "formatting": prepared.formatting,
            "nodes": prepared.nodes,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _prepared_arguments(prepared: PreparedBody, body_format: str) -> dict:
    return {
        "body": prepared.body,
        "body_format": body_format,
        "html": prepared.html,
        "formatting": prepared.formatting,
        "nodes": prepared.nodes,
    }


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


def send_chat(
    chat: WebChatRef, body: str, request_id: str, body_format: str = "plain"
) -> GroupReceipt:
    try:
        if ledger.has_request_id(request_id):
            raise TeamsError(
                "Request ID exists in the retired self-send ledger; do not retry or reuse it."
            )
    except ledger.SendLedgerError:
        raise TeamsError("Could not safely check prior send history.") from None
    try:
        prepared = format_body(body, body_format)
    except BodyFormatError as error:
        raise TeamsError(str(error)) from None
    path = (
        Path(os.environ["LOCALAPPDATA"])
        / "OfficeActionServer"
        / "teams-web-sends"
        / "group.sqlite3"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(_rich_identity(body, body_format, prepared).encode("utf-8")).hexdigest()
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
            {
                "chat": chat.model_dump(mode="json"),
                **_prepared_arguments(prepared, body_format),
                "request_id": request_id,
            },
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


def edit_chat(
    chat: WebChatRef,
    message_id: str,
    expected_text: str,
    body: str,
    request_id: str,
    body_format: str = "plain",
) -> EditReceipt:
    """Edit one verified own message under optimistic text and durable retry guards."""
    if not _MESSAGE_ID.fullmatch(message_id):
        raise TeamsError("Message ID is invalid.")
    if not isinstance(expected_text, str) or len(expected_text) > 2000:
        raise TeamsError("Expected message text must be at most 2000 characters.")
    try:
        if ledger.has_request_id(request_id):
            raise TeamsError(
                "Request ID exists in the retired self-send ledger; do not retry or reuse it."
            )
    except ledger.SendLedgerError:
        raise TeamsError("Could not safely check prior send history.") from None
    try:
        prepared = format_body(body, body_format)
    except BodyFormatError as error:
        raise TeamsError(str(error)) from None

    expected_sha = hashlib.sha256(expected_text.encode("utf-8")).hexdigest()
    digest = hashlib.sha256(_rich_identity(body, body_format, prepared).encode("utf-8")).hexdigest()
    path = (
        Path(os.environ["LOCALAPPDATA"])
        / "OfficeActionServer"
        / "teams-web-sends"
        / "group.sqlite3"
    )

    def cached_receipt(old):
        if old[:6] != (
            chat.chat_id,
            chat.title,
            message_id,
            expected_sha,
            body_format,
            digest,
        ):
            raise TeamsError(
                "Edit request ID conflicts with a prior destination or body; do not retry."
            )
        if old[6] != "edited" or not old[7]:
            raise TeamsError("Edit outcome is unknown; inspect the chat before retrying.")
        try:
            receipt = EditReceipt.model_validate_json(old[7])
        except ValueError:
            raise TeamsError(
                "Cached edit receipt is invalid; inspect the chat before retrying."
            ) from None
        if receipt.chat != chat or receipt.message_id != message_id:
            raise TeamsError("Cached edit receipt does not match the requested message.")
        return receipt.model_copy(update={"status": "already_edited"})

    select_edit = (
        "SELECT chat_id,chat_title,message_id,expected_sha256,body_format,digest,state,receipt "
        "FROM edits WHERE id=?"
    )
    if path.exists():
        try:
            with sqlite3.connect(path, timeout=3) as db:
                table = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='edits'"
                ).fetchone()
                old = db.execute(select_edit, (request_id,)).fetchone() if table else None
            if old:
                return cached_receipt(old)
        except TeamsError:
            raise
        except sqlite3.Error:
            raise TeamsError("Could not safely check prior edit history.") from None

    chat_data = chat.model_dump(mode="json")
    try:
        inspected = desktop.invoke(
            "teams_web_inspect_message", {"chat": chat_data, "message_id": message_id}
        )
    except desktop.BridgeError as error:
        raise TeamsError(f"Could not safely inspect the Teams message: {error.code}.") from None
    if (
        not isinstance(inspected, dict)
        or inspected.get("state") != "inspected"
        or inspected.get("chat_id") != chat.chat_id
        or inspected.get("title") != chat.title
        or inspected.get("message_id") != message_id
        or inspected.get("own_message") is not True
        or not isinstance(inspected.get("text"), str)
        or len(inspected["text"]) > 2000
        or inspected["text"] != expected_text
    ):
        raise TeamsError("The specified message is not an exact editable match in this chat.")

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sqlite3.connect(path, timeout=3) as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS edits (
                    id TEXT PRIMARY KEY,
                    chat_id TEXT NOT NULL,
                    chat_title TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    expected_sha256 TEXT NOT NULL,
                    body_format TEXT NOT NULL,
                    digest TEXT NOT NULL,
                    state TEXT NOT NULL,
                    receipt TEXT
                )"""
            )
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(select_edit, (request_id,)).fetchone()
            if old:
                return cached_receipt(old)
            db.execute(
                "INSERT INTO edits(id,chat_id,chat_title,message_id,expected_sha256,body_format,digest,state,receipt) "
                "VALUES (?,?,?,?,?,?,?,'reserved',NULL)",
                (
                    request_id,
                    chat.chat_id,
                    chat.title,
                    message_id,
                    expected_sha,
                    body_format,
                    digest,
                ),
            )
            db.commit()
    except TeamsError:
        raise
    except sqlite3.Error:
        raise TeamsError("Could not reserve the Teams edit safely.") from None

    arguments = {
        "chat": chat_data,
        "message_id": message_id,
        "expected_text": expected_text,
        **_prepared_arguments(prepared, body_format),
        "request_id": request_id,
    }
    try:
        result = desktop.invoke("teams_web_edit_message", arguments)
        if (
            result.get("state") not in {"edited", "already_edited"}
            or result.get("chat_id") != chat.chat_id
            or result.get("title") != chat.title
            or result.get("message_id") != message_id
            or not result.get("time")
        ):
            raise ValueError("unverified")
        receipt = EditReceipt(
            status=result["state"],
            chat=chat,
            message_id=message_id,
            time=result["time"],
        )
        with sqlite3.connect(path, timeout=3) as db:
            changed = db.execute(
                "UPDATE edits SET state='edited',receipt=? WHERE id=? AND state='reserved'",
                (receipt.model_dump_json(), request_id),
            ).rowcount
            if changed != 1:
                raise ValueError("reservation missing")
        return receipt
    except Exception:
        raise TeamsError("Edit outcome unknown; inspect the chat before retrying.") from None
