"""Resolve a previously returned display without repeating an Office action."""

import hashlib
import hmac
import json
import os
import sqlite3
import time
from contextlib import contextmanager

from pydantic import BaseModel

from office_bridge.presentation import MailActions, Presentation, View, present
from office_bridge.runtime.local_state import config_directory

TTL = 3600
MAX_VIEWS = 128
MAX_BYTES = 16 * 1024 * 1024
MAX_VIEW_BYTES = 1024 * 1024


class StoredView(BaseModel):
    view_id: str
    window: str
    created_at: float
    view: View
    mail: MailActions | None
    window_proof: str | None


def _key() -> tuple[bytes, str]:
    key = os.environ.get("OFFICE_CANVAS_SIGNING_KEY")
    if not key:
        raise ValueError("A managed Office Bridge session is required")
    encoded = key.encode()
    return encoded, hashlib.sha256(encoded).hexdigest()


@contextmanager
def _database():
    directory = config_directory()
    directory.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(directory / "window-views.sqlite3", timeout=3)
    try:
        db.execute(
            "CREATE TABLE IF NOT EXISTS views "
            "(scope TEXT, view_id TEXT, created REAL, payload TEXT, signature TEXT, "
            "PRIMARY KEY(scope, view_id))"
        )
        yield db
    finally:
        db.close()


def retain(display: Presentation) -> None:
    key, scope = _key()
    title = " ".join(display.view.title.split())[:100]
    count = len(display.view.cards)
    window = f"{display.view.source}: {title} ({count} {'message' if count == 1 else 'messages'})"
    record = StoredView(
        view_id=display.view_id,
        window=window,
        created_at=time.time(),
        view=display.view,
        mail=display.mail,
        window_proof=display.window_proof,
    )
    payload = json.dumps(record.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    if len(payload) > MAX_VIEW_BYTES:
        raise ValueError("This display exceeds the window size limit")
    signature = hmac.new(key, payload.encode(), "sha256").hexdigest()
    with _database() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM views WHERE created < ?", (time.time() - TTL,))
        db.execute("DELETE FROM views WHERE scope = ? AND view_id = ?", (scope, display.view_id))
        while True:
            count, size = db.execute(
                "SELECT count(*), coalesce(sum(length(payload)), 0) FROM views"
            ).fetchone()
            if count < MAX_VIEWS and size + len(payload) <= MAX_BYTES:
                break
            db.execute(
                "DELETE FROM views WHERE rowid = (SELECT rowid FROM views ORDER BY created LIMIT 1)"
            )
        db.execute(
            "INSERT INTO views VALUES (?, ?, ?, ?, ?)",
            (scope, display.view_id, record.created_at, payload, signature),
        )
        db.commit()
    display.window = window


def resolve(window: str, view_id: str) -> Presentation:
    key, scope = _key()
    with _database() as db:
        row = db.execute(
            "SELECT payload, signature FROM views WHERE scope = ? AND view_id = ?",
            (scope, view_id),
        ).fetchone()
    if not row:
        raise ValueError("This reading view is no longer available")
    payload, signature = row
    expected = hmac.new(key, payload.encode(), "sha256").hexdigest()
    if len(payload) > MAX_VIEW_BYTES or not hmac.compare_digest(expected, signature):
        raise ValueError("This reading view could not be verified")
    record = StoredView.model_validate_json(payload)
    if (
        record.view_id != view_id
        or record.window != window
        or not 0 <= time.time() - record.created_at <= TTL
    ):
        raise ValueError("This reading view expired or does not match the request")
    display = present(record.view)
    display.view_id, display.window = view_id, window
    display.mail, display.window_proof = record.mail, record.window_proof
    proof = display.proof()
    if display.mail and (not proof or not hmac.compare_digest(proof, display.window_proof or "")):
        raise ValueError("This reading view has no verified mail controls")
    return display
