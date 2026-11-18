"""Durable Outlook send reservations; uncertain submissions are never replayed."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Callable

from office_bridge.runtime.local_state import configured_account


class SendLedgerError(RuntimeError):
    """A bounded Outlook send reservation failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _root() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise SendLedgerError("send_ledger_unavailable")
    account = configured_account()
    account_key = hashlib.sha256(account.encode("utf-8")).hexdigest()
    return Path(local) / "OfficeActionServer" / "outlook-web-sends" / account_key


def _identifier(value: object) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 4096 and "\x00" not in value


def _digest(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _draft_key(draft: dict) -> str:
    identity = json.dumps(
        [draft["entry_id"], draft["store_id"]], ensure_ascii=True, separators=(",", ":")
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _connection(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        db = sqlite3.connect(path, timeout=3, isolation_level=None)
        db.execute("PRAGMA busy_timeout=3000")
        db.execute(
            """CREATE TABLE IF NOT EXISTS sends (
                draft_key TEXT PRIMARY KEY,
                digest TEXT NOT NULL,
                state TEXT NOT NULL,
                receipt TEXT
            )"""
        )
        return db
    except sqlite3.Error:
        raise SendLedgerError("send_ledger_unavailable") from None


def send_once(draft: dict, expected_digest: str, submit: Callable[[], dict]) -> dict:
    """Reserve a draft before submission and cache only a verified receipt."""
    if (
        not isinstance(draft, dict)
        or not _identifier(draft.get("entry_id"))
        or not _identifier(draft.get("store_id"))
        or not _digest(expected_digest)
        or not callable(submit)
    ):
        raise SendLedgerError("invalid_send_request")

    key = _draft_key(draft)
    path = _root() / "sends.sqlite3"
    db = _connection(path)
    try:
        try:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                "SELECT digest,state,receipt FROM sends WHERE draft_key=?", (key,)
            ).fetchone()
            if old:
                if old[1] == "verified":
                    if old[0] != expected_digest:
                        raise SendLedgerError("idempotency_conflict")
                    try:
                        receipt = json.loads(old[2])
                    except (TypeError, ValueError):
                        raise SendLedgerError("send_ledger_corrupt") from None
                    if not isinstance(receipt, dict):
                        raise SendLedgerError("send_ledger_corrupt")
                    db.execute("COMMIT")
                    return {**receipt, "status": "already_submitted"}
                if old[1] != "not_submitted":
                    raise SendLedgerError("mutation_outcome_unknown")
                db.execute(
                    "UPDATE sends SET digest=?,state='reserved',receipt=NULL WHERE draft_key=?",
                    (expected_digest, key),
                )
            else:
                db.execute(
                    "INSERT INTO sends(draft_key,digest,state,receipt) VALUES (?,?,?,NULL)",
                    (key, expected_digest, "reserved"),
                )
            db.execute("COMMIT")
        except SendLedgerError:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        except sqlite3.Error:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise SendLedgerError("send_ledger_unavailable") from None

        try:
            result = submit()
        except Exception:
            db.execute("UPDATE sends SET state='outcome_unknown' WHERE draft_key=?", (key,))
            raise SendLedgerError("mutation_outcome_unknown") from None

        proven_not_submitted = (
            isinstance(result, dict)
            and result.get("state") == "not_sent"
            and result.get("account_verified") is True
            and result.get("preflight_verified") is True
            and result.get("send_clicked") is False
            and result.get("draft_unchanged") is True
            and result.get("sent_items_verified") is False
            and result.get("draft") == draft
            and result.get("expected_digest") == expected_digest
        )
        if proven_not_submitted:
            db.execute(
                "UPDATE sends SET state='not_submitted',receipt=NULL WHERE draft_key=?", (key,)
            )
            raise SendLedgerError("send_not_submitted")

        submitted_at = result.get("submitted_at") if isinstance(result, dict) else None
        identity_ok = (
            isinstance(result, dict)
            and result.get("state") == "sent_verified"
            and result.get("account_verified") is True
            and result.get("sent_items_verified") is True
            and result.get("draft") == draft
            and isinstance(result.get("subject"), str)
            and len(result["subject"]) <= 250
            and "\x00" not in result["subject"]
            and isinstance(result.get("body_text"), str)
            and len(result["body_text"]) <= 20000
            and "\x00" not in result["body_text"]
            and isinstance(result.get("body_truncated"), bool)
            and isinstance(submitted_at, str)
            and len(submitted_at) <= 40
        )
        if identity_ok:
            try:
                datetime.fromisoformat(submitted_at.replace("Z", "+00:00"))
            except ValueError:
                identity_ok = False

        if not identity_ok:
            db.execute("UPDATE sends SET state='outcome_unknown' WHERE draft_key=?", (key,))
            raise SendLedgerError("mutation_outcome_unknown")

        receipt = {
            "draft": draft,
            "status": "submitted",
            "submitted_at": submitted_at,
            "subject": result["subject"],
            "body_text": result["body_text"],
            "body_truncated": result["body_truncated"],
            "account_verified": True,
            "sent_items_verified": True,
        }
        db.execute(
            "UPDATE sends SET state='verified',receipt=? WHERE draft_key=?",
            (json.dumps(receipt, separators=(",", ":")), key),
        )
        return receipt
    finally:
        db.close()
