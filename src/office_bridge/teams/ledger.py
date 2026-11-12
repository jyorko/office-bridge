"""Durable at-most-once guard for Teams sends.

The ledger stores hashes and bounded receipts only; message text is never written.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

LEDGER_PATH = (
    Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    / "OfficeActionServer"
    / "teams-web-sends"
    / "ledger.json"
)
LOCK_TIMEOUT_SECONDS = 3.0
_KEY_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$", re.ASCII)
_thread_lock = threading.Lock()


def expected_recipient() -> str:
    value = os.environ.get("OFFICE_BRIDGE_ACCOUNT", "").strip().lower()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
        raise SendLedgerError("expected_account_unconfigured")
    return value


class SendLedgerError(RuntimeError):
    """A bounded, non-sensitive error from the send ledger."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _file_lock(path: Path) -> Iterator[None]:
    """Acquire a cross-process one-byte lock, with a bounded wait."""

    @contextmanager
    def locked() -> Iterator[None]:
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        if not _thread_lock.acquire(timeout=LOCK_TIMEOUT_SECONDS):
            raise SendLedgerError("ledger_busy")
        handle = None
        acquired = False
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            lock_path = path.with_name(path.name + ".lock")
            handle = lock_path.open("a+b")
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            if os.name == "nt":
                import msvcrt

                while True:
                    try:
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        acquired = True
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise SendLedgerError("ledger_busy")
                        time.sleep(0.05)
            else:
                import fcntl

                while True:
                    try:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        acquired = True
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise SendLedgerError("ledger_busy")
                        time.sleep(0.05)
            yield
        finally:
            if handle is not None:
                try:
                    if acquired:
                        if os.name == "nt":
                            import msvcrt

                            handle.seek(0)
                            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                        else:
                            import fcntl

                            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                finally:
                    handle.close()
            _thread_lock.release()

    return locked()


def _read(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "entries": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(data, dict)
            or data.get("version") != 1
            or not isinstance(data.get("entries"), dict)
        ):
            raise ValueError
        recipient = expected_recipient()
        for key, entry in data["entries"].items():
            if (
                not isinstance(key, str)
                or not _KEY_RE.fullmatch(key)
                or not isinstance(entry, dict)
                or not isinstance(entry.get("body_sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", entry["body_sha256"])
                or entry.get("recipient") != recipient
                or entry.get("status")
                not in {"attempted", "outcome_unknown", "retryable_not_sent", "observed"}
                or not isinstance(entry.get("created_at"), str)
            ):
                raise ValueError
        return data
    except Exception:
        raise SendLedgerError("ledger_corrupt") from None


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=path.name + ".",
            suffix=".tmp",
            delete=False,
        ) as temp:
            temp_name = temp.name
            json.dump(data, temp, ensure_ascii=True, separators=(",", ":"))
            temp.flush()
            os.fsync(temp.fileno())
        os.replace(temp_name, path)
    except Exception:
        if temp_name:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
        raise SendLedgerError("ledger_write_failed") from None


_SAFE_RECEIPT_FIELDS = (
    "message_id",
    "recipient",
    "account_verified",
    "send_clicked",
    "composer_unchanged",
)


def _safe_receipt(result: dict, status: str) -> dict:
    receipt = {"status": status}
    for name in _SAFE_RECEIPT_FIELDS:
        value = result.get(name)
        if name == "message_id":
            if isinstance(value, str) and value and len(value) <= 256:
                receipt[name] = value
        elif name == "recipient":
            if isinstance(value, str) and len(value) <= 320:
                receipt[name] = value
        elif isinstance(value, bool):
            receipt[name] = value
    return receipt


def send_once(idempotency_key: str, body_text: str, submit: Callable[[], dict]) -> dict:
    """Attempt a send once; uncertain outcomes can never be retried automatically.

    A proven, untouched preflight failure may be retried by a later explicit call
    using the same key. Receipt data is deliberately whitelisted.
    """
    if not isinstance(idempotency_key, str) or not _KEY_RE.fullmatch(idempotency_key):
        raise SendLedgerError("invalid_idempotency_key")
    if not isinstance(body_text, str):
        raise SendLedgerError("invalid_body")
    if not callable(submit):
        raise SendLedgerError("invalid_submit")
    body_hash = hashlib.sha256(body_text.encode("utf-8")).hexdigest()
    recipient = expected_recipient()
    path = Path(LEDGER_PATH)
    with _file_lock(path):
        ledger = _read(path)
        entry = ledger["entries"].get(idempotency_key)
        if entry:
            if entry["body_sha256"] != body_hash:
                raise SendLedgerError("idempotency_conflict")
            if entry["status"] == "observed":
                cached = entry.get("receipt")
                if not isinstance(cached, dict):
                    raise SendLedgerError("ledger_corrupt")
                return {**cached, "status": "already_observed"}
            if entry["status"] != "retryable_not_sent":
                raise SendLedgerError("send_outcome_unknown")

        ledger["entries"][idempotency_key] = {
            "body_sha256": body_hash,
            "recipient": recipient,
            "created_at": entry["created_at"]
            if entry
            else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "status": "attempted",
        }
        _write(path, ledger)

        try:
            result = submit()
        except Exception:
            ledger["entries"][idempotency_key]["status"] = "outcome_unknown"
            _write(path, ledger)
            raise SendLedgerError("send_outcome_unknown") from None

        if not isinstance(result, dict):
            result = {}
        recipient = result.get("recipient")
        identity_ok = result.get("account_verified") is not False and (
            recipient is None or recipient == expected_recipient()
        )
        status = result.get("status")
        clicked = result.get("send_clicked") is True
        if identity_ok and status == "observed" and clicked:
            message_id = result.get("message_id")
            if isinstance(message_id, str) and message_id and len(message_id) <= 256:
                receipt = _safe_receipt(result, "observed")
                ledger["entries"][idempotency_key].update(status="observed", receipt=receipt)
                _write(path, ledger)
                return receipt

        if (
            identity_ok
            and status == "not_sent"
            and result.get("send_clicked") is False
            and result.get("composer_unchanged") is True
        ):
            receipt = _safe_receipt(result, "not_sent")
            ledger["entries"][idempotency_key].update(status="retryable_not_sent", receipt=receipt)
            _write(path, ledger)
            return receipt

        ledger["entries"][idempotency_key]["status"] = "outcome_unknown"
        _write(path, ledger)
        return _safe_receipt(result, "outcome_unknown")


def has_request_id(idempotency_key: str) -> bool:
    """Check the retired self-send ledger before using the generic chat ledger."""
    if not isinstance(idempotency_key, str) or not _KEY_RE.fullmatch(idempotency_key):
        raise SendLedgerError("invalid_idempotency_key")
    path = Path(LEDGER_PATH)
    with _file_lock(path):
        return idempotency_key in _read(path)["entries"]
