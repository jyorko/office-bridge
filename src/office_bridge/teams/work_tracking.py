"""Opt-in Eyes lifecycle, independent of disposable worker lifetimes.

Each account/message has its own coordinator lock and durable state database.
Pending transitions survive a crash and block every subsequent automatic click.
"""

import hashlib
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from office_bridge.runtime.local_state import configured_account
from office_bridge.teams import messages
from office_bridge.teams.models import WebChatRef
from office_bridge.teams.service import TeamsError

Outcome = Literal["completed", "paused", "cancelled", "failed"]
Marker = Literal[
    "unmarked", "owned", "preexisting", "cleared", "pending", "uncertain", "interference"
]


class TrackingPolicy(BaseModel):
    session_id: str
    enabled: bool
    chats: list[WebChatRef]


class WorkClaim(BaseModel):
    session_id: str
    work_id: str
    outcome: Literal["active", "completed", "paused", "cancelled", "failed"]


class WorkStatus(BaseModel):
    chat: WebChatRef
    message_id: str
    marker: Marker
    active_claims: int
    claims: list[WorkClaim]
    warning: str | None = None


def _key(value):
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", value):
        raise TeamsError("Session and work IDs must contain 1–128 letters, digits, . _ or -.")


def _root():
    try:
        account = configured_account()
    except ValueError:
        raise TeamsError("Configure the Teams account before enabling work tracking.") from None
    runtime_account = os.environ.get("OFFICE_BRIDGE_ACCOUNT")
    if runtime_account and runtime_account.strip().lower() != account:
        raise TeamsError("Teams account configuration changed; restart the managed server.")
    root = (
        Path(os.environ["LOCALAPPDATA"])
        / "OfficeActionServer"
        / "teams-web-sends"
        / "work-tracking"
        / hashlib.sha256(account.encode()).hexdigest()
    )
    root.mkdir(parents=True, exist_ok=True)
    return root


def _policy_db():
    db = sqlite3.connect(_root() / "policies.sqlite3", timeout=3)
    db.execute(
        "CREATE TABLE IF NOT EXISTS policies "
        "(session TEXT PRIMARY KEY, enabled INTEGER NOT NULL, chats TEXT NOT NULL)"
    )
    return db


def configure(session_id, chats, enabled):
    _key(session_id)
    if enabled and not chats:
        raise TeamsError("Select at least one exact chat before enabling tracking.")
    if len({c.chat_id for c in chats}) != len(chats):
        raise TeamsError("Each chat may appear once in a tracking policy.")
    refs = [c.model_dump(mode="json") for c in chats]
    with _policy_db() as db:
        old = db.execute("SELECT chats FROM policies WHERE session=?", (session_id,)).fetchone()
        if old and json.loads(old[0]) != refs:
            raise TeamsError("Use a new session ID to change the authorized chat scope.")
        db.execute(
            "INSERT INTO policies VALUES (?,?,?) ON CONFLICT(session) "
            "DO UPDATE SET enabled=excluded.enabled",
            (session_id, enabled, json.dumps(refs)),
        )
    return TrackingPolicy(session_id=session_id, enabled=enabled, chats=chats)


def _authorized(session_id, chat, *, starting):
    _key(session_id)
    with _policy_db() as db:
        row = db.execute(
            "SELECT enabled,chats FROM policies WHERE session=?", (session_id,)
        ).fetchone()
    if not row or (starting and not row[0]) or chat.model_dump() not in json.loads(row[1]):
        raise TeamsError("Eyes tracking is not opted in for this session and exact chat.")


@contextmanager
def _coordinator(chat, message_id):
    if not re.fullmatch(r"message-body-[0-9]{13}", message_id):
        raise TeamsError("Invalid message ID.")
    key = hashlib.sha256(json.dumps([chat.chat_id, message_id]).encode()).hexdigest()
    root = _root()
    lock = sqlite3.connect(root / (key + ".lock.sqlite3"), timeout=60)
    db = None
    try:
        lock.execute("CREATE TABLE IF NOT EXISTS coordinator (id INTEGER PRIMARY KEY)")
        lock.commit()
        lock.execute("BEGIN IMMEDIATE")
        db = sqlite3.connect(root / (key + ".sqlite3"), timeout=3)
        db.row_factory = sqlite3.Row
        db.executescript(
            "CREATE TABLE IF NOT EXISTS marker "
            "(id INTEGER PRIMARY KEY CHECK(id=1), title TEXT NOT NULL, content TEXT NOT NULL, "
            "state TEXT NOT NULL, generation INTEGER NOT NULL);"
            "CREATE TABLE IF NOT EXISTS claims "
            "(session TEXT NOT NULL, work TEXT NOT NULL, outcome TEXT NOT NULL, "
            "PRIMARY KEY(session,work));"
            "CREATE TABLE IF NOT EXISTS transitions "
            "(generation INTEGER PRIMARY KEY, request TEXT NOT NULL, desired INTEGER NOT NULL, "
            "result TEXT);"
        )
        existing = db.execute("SELECT title FROM marker").fetchone()
        if existing and existing["title"] != chat.title:
            raise TeamsError("Tracked chat title changed; automatic reaction changes are stopped.")
        yield db, key
    except sqlite3.Error:
        raise TeamsError(
            "Work tracking coordinator is busy or unavailable; no retry was issued."
        ) from None
    finally:
        if db is not None:
            db.close()
        lock.close()


def _state(db, state):
    db.execute("UPDATE marker SET state=? WHERE id=1", (state,))
    db.commit()


def _status(db, chat, message_id, warning=None):
    marker = db.execute("SELECT state FROM marker WHERE id=1").fetchone()
    claims = [
        WorkClaim(session_id=r["session"], work_id=r["work"], outcome=r["outcome"])
        for r in db.execute("SELECT * FROM claims ORDER BY session,work")
    ]
    state = marker["state"] if marker else "unmarked"
    if state in {"pending", "uncertain"}:
        warning = "Reaction outcome is uncertain. Automatic add/remove is blocked; do not retry."
    elif state == "interference":
        warning = "Message content or reaction changed externally; automatic cleanup is stopped."
    return WorkStatus(
        chat=chat,
        message_id=message_id,
        marker=state,
        claims=claims,
        active_claims=sum(c.outcome == "active" for c in claims),
        warning=warning,
    )


def _eyes(snapshot):
    choices = [c for c in snapshot["reactions"] if c.get("emoji_id") == "1f440_eyes"]
    if len(choices) != 1:
        return None
    return choices[0]["selected"]


def _transition(db, key, chat, message_id, content, selected):
    generation = db.execute("SELECT generation FROM marker WHERE id=1").fetchone()[0] + 1
    request_id = f"work-eyes-{key}-{generation}"
    db.execute("INSERT INTO transitions VALUES (?,?,?,NULL)", (generation, request_id, selected))
    db.execute("UPDATE marker SET state='pending',generation=? WHERE id=1", (generation,))
    db.commit()
    try:
        result = messages.set_eyes(chat, message_id, content, selected, request_id)
    except TeamsError:
        _state(db, "uncertain")
        return
    db.execute("UPDATE transitions SET result=? WHERE generation=?", (result, generation))
    # A no-click add establishes no ownership, even if the baseline was unselected.
    state = ("owned" if result == "reacted" else "preexisting") if selected else "cleared"
    db.execute("UPDATE marker SET state=? WHERE id=1", (state,))
    db.commit()


def begin(session_id, work_id, chat, message_id, expected_content):
    _key(work_id)
    _authorized(session_id, chat, starting=True)
    with _coordinator(chat, message_id) as (db, key):
        old = db.execute(
            "SELECT outcome FROM claims WHERE session=? AND work=?", (session_id, work_id)
        ).fetchone()
        if old:
            # A completed claim is never silently reactivated.
            return _status(db, chat, message_id)
        row = db.execute("SELECT * FROM marker WHERE id=1").fetchone()
        if row and row["state"] in {"pending", "uncertain", "interference"}:
            return _status(db, chat, message_id)
        try:
            snapshot = messages.observe(chat, message_id)
        except TeamsError:
            return _status(
                db, chat, message_id, "Could not inspect Eyes; continue work without a marker."
            )
        content = snapshot["content_text"]
        if content != expected_content:
            raise TeamsError("The task message content changed; no work marker was added.")
        active = db.execute("SELECT COUNT(*) FROM claims WHERE outcome='active'").fetchone()[0]
        if row and not active and row["state"] == "owned":
            return _status(
                db,
                chat,
                message_id,
                "An ended claim still owns Eyes; finish that claim again to reconcile cleanup.",
            )
        if row and active and row["content"] != content:
            _state(db, "interference")
            return _status(db, chat, message_id)
        selected = _eyes(snapshot)
        if row and active and row["state"] == "owned" and selected is not True:
            _state(db, "interference")
            return _status(db, chat, message_id)
        if not active:
            db.execute(
                "INSERT INTO marker VALUES (1,?,?,'unmarked',0) ON CONFLICT(id) "
                "DO UPDATE SET content=excluded.content,state='unmarked'",
                (chat.title, content),
            )
        db.execute("INSERT INTO claims VALUES (?,?,'active')", (session_id, work_id))
        db.commit()
        if active:
            return _status(db, chat, message_id)
        if selected is True:
            _state(db, "preexisting")
        elif selected is False:
            _transition(db, key, chat, message_id, content, True)
        return _status(
            db,
            chat,
            message_id,
            "Eyes is not available in the loaded picker; work is tracked without a reaction."
            if selected is None
            else None,
        )


def finish(session_id, work_id, chat, message_id, outcome):
    _key(work_id)
    if outcome not in {"completed", "paused", "cancelled", "failed"}:
        raise TeamsError("A terminal work outcome is required.")
    _authorized(session_id, chat, starting=False)
    with _coordinator(chat, message_id) as (db, key):
        old = db.execute(
            "SELECT outcome FROM claims WHERE session=? AND work=?", (session_id, work_id)
        ).fetchone()
        if not old:
            raise TeamsError("This exact work claim was never started.")
        if old["outcome"] != "active":
            if old["outcome"] != outcome:
                raise TeamsError("Work was already finished with a different outcome.")
        else:
            db.execute(
                "UPDATE claims SET outcome=? WHERE session=? AND work=?",
                (outcome, session_id, work_id),
            )
            db.commit()
        status = _status(db, chat, message_id)
        if status.active_claims or status.marker != "owned":
            return status
        row = db.execute("SELECT content FROM marker WHERE id=1").fetchone()
        try:
            snapshot = messages.observe(chat, message_id)
        except TeamsError:
            _state(db, "uncertain")
            return _status(db, chat, message_id)
        if snapshot["content_text"] != row["content"] or _eyes(snapshot) is not True:
            _state(db, "interference")
            return _status(db, chat, message_id)
        _transition(db, key, chat, message_id, row["content"], False)
        return _status(db, chat, message_id)


def status(chat, message_id):
    """Read durable claims and reconcile observations without any Teams mutation."""
    with _coordinator(chat, message_id) as (db, _):
        result = _status(db, chat, message_id)
        if result.marker not in {"owned", "preexisting"}:
            return result
        snapshot = messages.observe(chat, message_id)
        content = db.execute("SELECT content FROM marker WHERE id=1").fetchone()[0]
        if snapshot["content_text"] != content or _eyes(snapshot) is not True:
            _state(db, "interference")
        return _status(db, chat, message_id)
