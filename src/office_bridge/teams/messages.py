"""Exact-message links and durably reserved reactions."""

import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from office_bridge import desktop
from office_bridge.teams.models import WebChatRef
from office_bridge.teams.service import TeamsError


class MessageLink(BaseModel):
    chat: WebChatRef
    message_id: str
    url: str = Field(pattern=r"^https://teams\.microsoft\.com/l/message/")


class ReactionChoice(BaseModel):
    name: str
    selected: bool


class MessageReactions(BaseModel):
    chat: WebChatRef
    message_id: str
    reactions: list[ReactionChoice]


class ReactionReceipt(BaseModel):
    chat: WebChatRef
    message_id: str
    reaction: str
    status: Literal["reacted", "already_reacted"]


def _invoke(operation, chat, message_id, **arguments):
    if not re.fullmatch(r"message-body-[0-9]{13}", message_id):
        raise TeamsError("Invalid message ID.")
    try:
        result = desktop.invoke(
            operation,
            {"chat": chat.model_dump(mode="json"), "message_id": message_id, **arguments},
        )
    except desktop.BridgeError as error:
        raise TeamsError(f"Message operation stopped: {error.code}.") from None
    if (
        result.get("chat_id") != chat.chat_id
        or result.get("title") != chat.title
        or result.get("message_id") != message_id
    ):
        raise TeamsError("The message identity could not be verified.")
    return result


def link(chat, message_id):
    result = _invoke("teams_web_message_link", chat, message_id)
    if result.get("state") != "linked":
        raise TeamsError("The message link could not be verified.")
    return MessageLink(chat=chat, message_id=message_id, url=result["url"])


def reactions(chat, message_id, expected_text):
    result = _invoke("teams_web_message_reactions", chat, message_id, expected_text=expected_text)
    if result.get("state") != "reactions":
        raise TeamsError("Message reaction choices could not be verified.")
    return MessageReactions(chat=chat, message_id=message_id, reactions=result["reactions"])


def react(chat, message_id, expected_text, reaction, request_id):
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", request_id):
        raise TeamsError("Invalid reaction request ID.")
    if not isinstance(expected_text, str) or len(expected_text) > 2000:
        raise TeamsError("Expected text must be at most 2000 characters.")
    identity = json.dumps([chat.model_dump(), message_id, expected_text, reaction], sort_keys=True)
    digest = hashlib.sha256(identity.encode()).hexdigest()
    path = Path(os.environ["LOCALAPPDATA"]) / "OfficeActionServer/teams-web-sends/group.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path, timeout=3) as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS reactions "
            "(id TEXT PRIMARY KEY, digest TEXT NOT NULL, receipt TEXT)"
        )
        db.execute("BEGIN IMMEDIATE")
        old = db.execute(
            "SELECT digest,receipt FROM reactions WHERE id=?", (request_id,)
        ).fetchone()
        if old:
            if old[0] != digest:
                raise TeamsError("Reaction request ID conflicts with an earlier request.")
            if not old[1]:
                raise TeamsError("Reaction outcome is uncertain; do not retry.")
            return ReactionReceipt.model_validate_json(old[1])
        db.execute("INSERT INTO reactions VALUES (?,?,NULL)", (request_id, digest))
    result = _invoke(
        "teams_web_react_message",
        chat,
        message_id,
        expected_text=expected_text,
        reaction=reaction,
        request_id=request_id,
    )
    if (
        result.get("state") not in {"reacted", "already_reacted"}
        or result.get("reaction") != reaction
    ):
        raise TeamsError("Reaction outcome is uncertain; inspect the message without retrying.")
    receipt = ReactionReceipt(
        chat=chat, message_id=message_id, reaction=reaction, status=result["state"]
    )
    with sqlite3.connect(path, timeout=3) as db:
        db.execute(
            "UPDATE reactions SET receipt=? WHERE id=?", (receipt.model_dump_json(), request_id)
        )
    return receipt
