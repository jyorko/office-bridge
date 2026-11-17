"""Exact-message links and durably reserved reactions."""

import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

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
    emoji_id: str = ""


class MessageReactions(BaseModel):
    chat: WebChatRef
    message_id: str
    reactions: list[ReactionChoice]
    content_text: str = ""


class ReactionReceipt(BaseModel):
    chat: WebChatRef
    message_id: str
    reaction: str
    status: Literal["reacted", "already_reacted"]


class MentionValueShape(BaseModel):
    prefix_class: Literal["empty", "email", "hex-or-guid", "numeric", "url", "identifier", "text"]
    length: int = Field(ge=0, le=4096)
    hash: str = Field(pattern=r"^[0-9a-f]{8}$")


_SAFE_ARIA_ROLES = {
    "alert",
    "alertdialog",
    "application",
    "article",
    "banner",
    "blockquote",
    "button",
    "caption",
    "cell",
    "checkbox",
    "code",
    "columnheader",
    "combobox",
    "complementary",
    "contentinfo",
    "definition",
    "deletion",
    "dialog",
    "directory",
    "document",
    "emphasis",
    "feed",
    "figure",
    "form",
    "generic",
    "grid",
    "gridcell",
    "group",
    "heading",
    "img",
    "insertion",
    "link",
    "list",
    "listbox",
    "listitem",
    "log",
    "main",
    "marquee",
    "math",
    "menu",
    "menubar",
    "menuitem",
    "menuitemcheckbox",
    "menuitemradio",
    "meter",
    "navigation",
    "none",
    "note",
    "option",
    "paragraph",
    "presentation",
    "progressbar",
    "radio",
    "radiogroup",
    "region",
    "row",
    "rowgroup",
    "rowheader",
    "scrollbar",
    "search",
    "searchbox",
    "separator",
    "slider",
    "spinbutton",
    "status",
    "strong",
    "subscript",
    "superscript",
    "switch",
    "tab",
    "table",
    "tabpanel",
    "term",
    "textbox",
    "time",
    "timer",
    "toolbar",
    "tooltip",
    "tree",
    "treegrid",
    "treeitem",
}


class MentionAttributeEvidence(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    value_shape: MentionValueShape


class MentionEntityEvidence(BaseModel):
    tag: str = Field(min_length=1, max_length=30)
    depth: int = Field(ge=0, le=19)
    ancestors: list[str] = Field(max_length=12)
    attribute_names: list[str] = Field(max_length=30)
    attributes: list[MentionAttributeEvidence] = Field(max_length=30)
    role: str = Field(max_length=40)
    role_shape: MentionValueShape
    itemtype: str = Field(max_length=80)
    data_tid_shape: MentionValueShape

    @field_validator("role")
    @classmethod
    def role_is_semantic(cls, value: str) -> str:
        if value and value not in _SAFE_ARIA_ROLES:
            raise ValueError("Unrecognized ARIA role must be redacted.")
        return value


class MentionEvidenceReport(BaseModel):
    """Sanitized DOM structure for a settled mention; it contains no identity values or text."""

    state: Literal["inspected", "no_mentions"]
    capture_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    mentions: list[MentionEntityEvidence] = Field(max_length=20)

    @model_validator(mode="after")
    def state_matches_evidence(self):
        if (self.state == "inspected") != bool(self.mentions):
            raise ValueError("Mention evidence state does not match its entities.")
        return self


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


def inspect_mention_evidence(chat, message_id):
    """Return only bounded, anonymized structure from one exact existing message."""
    result = _invoke(
        "teams_web_inspect_mention_evidence",
        chat,
        message_id,
    )
    if result.get("state") not in {"inspected", "no_mentions"}:
        raise TeamsError("Mention entity evidence is unavailable for that message.")
    try:
        return MentionEvidenceReport.model_validate(
            {
                "state": result["state"],
                "capture_date": result["capture_date"],
                "mentions": result["mentions"],
            }
        )
    except (KeyError, TypeError, ValueError):
        raise TeamsError("Mention entity evidence could not be safely sanitized.") from None


def link(chat, message_id):
    result = _invoke("teams_web_message_link", chat, message_id)
    if result.get("state") != "linked":
        raise TeamsError("The message link could not be verified.")
    return MessageLink(chat=chat, message_id=message_id, url=result["url"])


def reactions(chat, message_id, expected_text):
    result = _invoke("teams_web_message_reactions", chat, message_id, expected_text=expected_text)
    if result.get("state") != "reactions":
        raise TeamsError("Message reaction choices could not be verified.")
    return MessageReactions(
        chat=chat,
        message_id=message_id,
        reactions=result["reactions"],
        content_text=result.get("content_text", ""),
    )


def observe(chat, message_id):
    """Read current content and controls without reusing historical text."""
    result = _invoke(
        "teams_web_message_reactions", chat, message_id, expected_text="", read_current=True
    )
    if result.get("state") != "reactions" or not isinstance(result.get("content_text"), str):
        raise TeamsError("Current message content could not be verified.")
    return result


def set_eyes(chat, message_id, content, selected, request_id):
    """Internal desired-state transition; lifecycle ownership controls removal."""
    result = _invoke(
        "teams_web_set_reaction",
        chat,
        message_id,
        expected_text=content,
        reaction="Eyes",
        emoji_id="1f440_eyes",
        selected=selected,
        request_id=request_id,
    )
    allowed = {"reacted", "already_reacted"} if selected else {"removed", "already_removed"}
    if result.get("state") not in allowed or result.get("reaction") != "Eyes":
        raise TeamsError("Eyes transition is uncertain; do not retry.")
    return result["state"]


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
