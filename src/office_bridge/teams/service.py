"""Teams operations and durable send reservations, independent of action decorators."""

import hashlib
import json
import os
import re
import sqlite3
import stat
from pathlib import Path

from office_bridge import desktop
from office_bridge.teams import ledger
from office_bridge.teams.formatting import (
    BodyFormatError,
    PreparedBody,
    format_body,
    validate_mention_placeholders,
)
from office_bridge.teams.models import (
    EditReceipt,
    GroupReceipt,
    PersonMentionReceipt,
    PersonMentions,
    UploadReadiness,
    UploadReceipt,
    UploadStatus,
    WebChatHistory,
    WebChatMessage,
    WebChatRef,
    WebChatSearchResult,
)


class TeamsError(RuntimeError):
    """Bounded, user-facing operation failure."""


_MESSAGE_ID = re.compile(r"^message-body-[0-9]{13}$")
_UPLOAD_NAME = re.compile(r'^[^\\/:*?"<>|\x00-\x1f]{1,180}$')
_UPLOAD_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_MAX_UPLOAD_FILES = 10
_MAX_UPLOAD_FILE_BYTES = 25 * 1024 * 1024
_MAX_UPLOAD_TOTAL_BYTES = 50 * 1024 * 1024


def _upload_files(file_paths: list[str]) -> list[dict]:
    """Validate bounded local files and bind the request to their content hashes."""
    if not isinstance(file_paths, list) or not 1 <= len(file_paths) <= _MAX_UPLOAD_FILES:
        raise TeamsError("Attach between 1 and 10 local files.")
    files = []
    total = 0
    names: set[str] = set()
    for raw_path in file_paths:
        if (
            not isinstance(raw_path, str)
            or not raw_path
            or len(raw_path) > 32767
            or "\x00" in raw_path
            or raw_path.startswith(("\\\\", "//", "\\\\?\\", "\\\\.\\"))
        ):
            raise TeamsError("A file path is invalid; use an absolute local file path.")
        path = Path(raw_path)
        if not path.is_absolute():
            raise TeamsError("A file path is invalid; use an absolute local file path.")
        try:
            if path.is_symlink():
                raise TeamsError("Symbolic-link files cannot be attached.")
            resolved = path.resolve(strict=True)
            metadata = resolved.stat()
        except TeamsError:
            raise
        except (OSError, RuntimeError, ValueError):
            raise TeamsError("A local file could not be opened safely.") from None
        attributes = getattr(metadata, "st_file_attributes", 0)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or (attributes & 0x400) != 0
            or (os.name == "nt" and (not resolved.drive or resolved.drive.startswith("\\\\")))
        ):
            raise TeamsError("Each attachment must be a regular local file.")
        name = resolved.name
        base_name = name.split(".", 1)[0].rstrip(" .").upper()
        if (
            not _UPLOAD_NAME.fullmatch(name)
            or name.endswith((".", " "))
            or base_name in _UPLOAD_RESERVED_NAMES
            or name in {".", ".."}
        ):
            raise TeamsError("A file name is not safe to attach.")
        folded = name.casefold()
        if folded in names:
            raise TeamsError("Attachment file names must be unique.")
        names.add(folded)
        size = metadata.st_size
        modified_ns = getattr(metadata, "st_mtime_ns", None)
        total += size
        if size < 0 or size > _MAX_UPLOAD_FILE_BYTES or total > _MAX_UPLOAD_TOTAL_BYTES:
            raise TeamsError("Attachments exceed the 25 MiB per-file or 50 MiB total limit.")
        digest = hashlib.sha256()
        try:
            with resolved.open("rb") as stream:
                opened = os.fstat(stream.fileno())
                if not stat.S_ISREG(opened.st_mode) or opened.st_size != size:
                    raise TeamsError("A local file changed while it was being checked.")
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
                after = os.fstat(stream.fileno())
            current = resolved.stat()
        except TeamsError:
            raise
        except OSError:
            raise TeamsError("A local file could not be read safely.") from None
        if (
            after.st_size != size
            or current.st_size != size
            or getattr(after, "st_mtime_ns", None) != modified_ns
            or getattr(current, "st_mtime_ns", None) != modified_ns
            or getattr(current, "st_ino", None) != getattr(metadata, "st_ino", None)
        ):
            raise TeamsError("A local file changed while it was being checked.")
        files.append(
            {
                "path": str(resolved),
                "name": name,
                "size_bytes": size,
                "sha256": digest.hexdigest(),
            }
        )
    return files


def _upload_path() -> Path:
    return (
        Path(os.environ["LOCALAPPDATA"])
        / "OfficeActionServer"
        / "teams-web-sends"
        / "group.sqlite3"
    )


def _ensure_upload_table(db: sqlite3.Connection) -> None:
    db.execute(
        """CREATE TABLE IF NOT EXISTS uploads (
            id TEXT PRIMARY KEY,
            chat_id TEXT NOT NULL,
            chat_title TEXT NOT NULL,
            digest TEXT NOT NULL,
            state TEXT NOT NULL,
            file_count INTEGER NOT NULL,
            receipt TEXT
        )"""
    )


def _upload_digest(chat: WebChatRef, files: list[dict]) -> str:
    data = {
        "chat": chat.model_dump(mode="json"),
        "files": [
            {"name": item["name"], "size_bytes": item["size_bytes"], "sha256": item["sha256"]}
            for item in files
        ],
    }
    return hashlib.sha256(
        json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


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


def _mention_plan_identity(mentions: PersonMentions) -> list[dict[str, object]]:
    return [
        {
            "key": mention.key,
            "display_label": mention.display_label,
            "recipient_email": mention.recipient.email,
        }
        for mention in mentions.root
    ]


def _require_mention_capability() -> None:
    try:
        status = desktop.invoke("teams_web_status", {})
    except desktop.BridgeError:
        raise TeamsError(
            "Could not verify a mention-capable Teams host; no message was sent."
        ) from None
    capabilities = status.get("capabilities")
    if (
        status.get("state") != "authenticated"
        or status.get("account_verified") is not True
        or status.get("host_visible") is not False
        or status.get("control_visible") is not False
        or status.get("foreground_interference") is not False
        or status.get("restart_required") is not False
        or not isinstance(capabilities, list)
        or "person_mentions_v1" not in capabilities
    ):
        raise TeamsError("Teams host does not support verified person mentions; reload required.")


def _reserve_send(
    path: Path, request_id: str, digest: str, chat: WebChatRef
) -> GroupReceipt | None:
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
    return None


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
    chat: WebChatRef,
    body: str,
    request_id: str,
    body_format: str = "plain",
    mentions: PersonMentions = PersonMentions([]),
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
    mention_keys = {mention.key for mention in mentions.root}
    try:
        placeholder_order = validate_mention_placeholders(body, body_format, mention_keys)
    except BodyFormatError as error:
        raise TeamsError(str(error)) from None
    if mentions.root:
        if placeholder_order != [mention.key for mention in mentions.root]:
            raise TeamsError("Mention plans must follow placeholder order in the message.")
        if body_format != "plain":
            raise TeamsError("Person mentions currently require a plain-text body.")
        expected_body = prepared.body
        for mention in mentions.root:
            expected_body = expected_body.replace(
                "{{mention:" + mention.key + "}}", mention.display_label
            )
        if len(expected_body) > 2000:
            raise TeamsError("Message body with mention labels must be at most 2000 characters.")
        _require_mention_capability()
    path = (
        Path(os.environ["LOCALAPPDATA"])
        / "OfficeActionServer"
        / "teams-web-sends"
        / "group.sqlite3"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    identity = _rich_identity(body, body_format, prepared)
    if mentions.root:
        identity = json.dumps(
            {"body_identity": identity, "mentions": _mention_plan_identity(mentions)},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    cached = _reserve_send(path, request_id, digest, chat)
    if cached is not None:
        return cached
    try:
        arguments = {
            "chat": chat.model_dump(mode="json"),
            **_prepared_arguments(prepared, body_format),
            "request_id": request_id,
        }
        if mentions.root:
            arguments["mentions"] = _mention_plan_identity(mentions)
            arguments["visible_body"] = expected_body
        result = desktop.invoke("teams_web_send_chat", arguments)
        if (
            result.get("state") != "observed"
            or not result.get("message_id")
            or not result.get("time")
            or result.get("chat_id") != chat.chat_id
            or result.get("title") != chat.title
        ):
            raise ValueError("unverified")
        receipt_mentions = []
        if mentions.root:
            raw_mentions = result.get("mentions")
            if not isinstance(raw_mentions, list) or len(raw_mentions) != len(mentions.root):
                raise ValueError("mention entities unverified")
            for expected, actual in zip(mentions.root, raw_mentions, strict=True):
                item = PersonMentionReceipt.model_validate(actual)
                if (
                    item.key != expected.key
                    or item.text != expected.display_label
                    or item.recipient_email.casefold() != expected.recipient.email.casefold()
                    or not item.identity_evidence
                    or item.entity_verified is not True
                ):
                    raise ValueError("mention entity mismatch")
                receipt_mentions.append(item)
        receipt = GroupReceipt(
            status="observed",
            chat=chat,
            message_id=result["message_id"],
            time=result["time"],
            mentions=receipt_mentions,
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


def upload_readiness(chat: WebChatRef) -> UploadReadiness:
    """Inspect the exact chat's hidden composer without changing its draft."""
    try:
        result = desktop.invoke(
            "teams_web_upload_readiness", {"chat": chat.model_dump(mode="json")}
        )
        if (
            not isinstance(result, dict)
            or result.get("chat_id") != chat.chat_id
            or result.get("title") != chat.title
        ):
            raise ValueError("chat identity mismatch")
        return UploadReadiness.model_validate({**result, "chat": chat})
    except desktop.BridgeError as error:
        raise TeamsError(f"Could not inspect Teams file-upload readiness: {error.code}.") from None
    except (KeyError, ValueError):
        raise TeamsError("Could not verify Teams file-upload readiness.") from None


def _cached_upload(row: tuple, chat: WebChatRef, digest: str) -> UploadReceipt:
    if row[:3] != (chat.chat_id, chat.title, digest):
        raise TeamsError("Upload request ID conflicts with a prior destination or file set.")
    if row[3] != "uploaded" or not row[4]:
        raise TeamsError(
            "Upload outcome is unknown; use teams_web_upload_status and inspect the chat before retrying."
        )
    try:
        receipt = UploadReceipt.model_validate_json(row[4])
    except ValueError:
        raise TeamsError(
            "Cached upload receipt is invalid; inspect the chat before retrying."
        ) from None
    if receipt.chat != chat:
        raise TeamsError("Cached upload receipt does not match the requested chat.")
    return receipt.model_copy(update={"status": "already_uploaded"})


def _record_upload_receipt(
    path: Path, request_id: str, chat: WebChatRef, result: dict, expected_names: list[str]
) -> UploadReceipt:
    if (
        not isinstance(result, dict)
        or result.get("state") != "uploaded"
        or result.get("chat_id") != chat.chat_id
        or result.get("title") != chat.title
        or not isinstance(result.get("message_id"), str)
        or not _MESSAGE_ID.fullmatch(result["message_id"])
        or not isinstance(result.get("time"), str)
        or not result.get("time")
        or result.get("files") != expected_names
    ):
        raise ValueError("unverified upload receipt")
    receipt = UploadReceipt(
        status="uploaded",
        chat=chat,
        message_id=result["message_id"],
        time=result["time"],
        files=expected_names,
    )
    with sqlite3.connect(path, timeout=3) as db:
        changed = db.execute(
            "UPDATE uploads SET state='uploaded',receipt=? WHERE id=? AND state='reserved'",
            (receipt.model_dump_json(), request_id),
        ).rowcount
        if changed != 1:
            raise ValueError("upload reservation missing")
    return receipt


def upload_files(chat: WebChatRef, file_paths: list[str], request_id: str) -> UploadReceipt:
    """Upload a bounded local file set once to one verified Teams chat."""
    if not isinstance(request_id, str) or not re.fullmatch(r"^[A-Za-z0-9._-]{1,128}$", request_id):
        raise TeamsError("Upload request ID is invalid.")
    try:
        if ledger.has_request_id(request_id):
            raise TeamsError("Request ID exists in the retired send ledger; do not reuse it.")
    except ledger.SendLedgerError:
        raise TeamsError("Could not safely check prior send history.") from None

    files = _upload_files(file_paths)
    digest = _upload_digest(chat, files)
    path = _upload_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.exists():
            with sqlite3.connect(path, timeout=3) as db:
                table = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='uploads'"
                ).fetchone()
                old = (
                    db.execute(
                        "SELECT chat_id,chat_title,digest,state,receipt FROM uploads WHERE id=?",
                        (request_id,),
                    ).fetchone()
                    if table
                    else None
                )
            if old:
                return _cached_upload(old, chat, digest)
    except TeamsError:
        raise
    except sqlite3.Error:
        raise TeamsError("Could not safely check prior upload history.") from None

    ready = upload_readiness(chat)
    if ready.state != "ready":
        raise TeamsError(
            "Teams is not ready for an upload; the existing composer was left unchanged."
        )
    try:
        with sqlite3.connect(path, timeout=3) as db:
            _ensure_upload_table(db)
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                "SELECT chat_id,chat_title,digest,state,receipt FROM uploads WHERE id=?",
                (request_id,),
            ).fetchone()
            if old:
                return _cached_upload(old, chat, digest)
            db.execute(
                "INSERT INTO uploads(id,chat_id,chat_title,digest,state,file_count,receipt) "
                "VALUES (?,?,?,?, 'reserved', ?, NULL)",
                (request_id, chat.chat_id, chat.title, digest, len(files)),
            )
            db.commit()
    except TeamsError:
        raise
    except sqlite3.Error:
        raise TeamsError("Could not reserve the Teams upload safely.") from None

    try:
        result = desktop.invoke(
            "teams_web_upload_files",
            {
                "chat": chat.model_dump(mode="json"),
                "files": files,
                "request_id": request_id,
            },
        )
        return _record_upload_receipt(
            path, request_id, chat, result, [item["name"] for item in files]
        )
    except Exception:
        raise TeamsError(
            "Upload outcome unknown; use teams_web_upload_status and inspect the chat before retrying."
        ) from None


def upload_status(chat: WebChatRef, request_id: str) -> UploadStatus:
    """Reconcile a durable upload receipt without clicking or replaying."""
    if not isinstance(request_id, str) or not re.fullmatch(r"^[A-Za-z0-9._-]{1,128}$", request_id):
        raise TeamsError("Upload request ID is invalid.")
    path = _upload_path()
    if not path.exists():
        return UploadStatus(status="not_found", chat=chat, request_id=request_id, file_count=0)
    try:
        with sqlite3.connect(path, timeout=3) as db:
            table = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='uploads'"
            ).fetchone()
            row = (
                db.execute(
                    "SELECT chat_id,chat_title,digest,state,file_count,receipt "
                    "FROM uploads WHERE id=?",
                    (request_id,),
                ).fetchone()
                if table
                else None
            )
    except sqlite3.Error:
        raise TeamsError("Could not safely read upload history.") from None
    if not row:
        return UploadStatus(status="not_found", chat=chat, request_id=request_id, file_count=0)
    if (row[0], row[1]) != (chat.chat_id, chat.title):
        raise TeamsError("Upload request ID belongs to a different chat.")
    if row[3] == "uploaded":
        try:
            receipt = UploadReceipt.model_validate_json(row[5])
        except (TypeError, ValueError):
            raise TeamsError(
                "Cached upload receipt is invalid; inspect the chat manually."
            ) from None
        return UploadStatus(
            status="uploaded",
            chat=chat,
            request_id=request_id,
            file_count=row[4],
            receipt=receipt,
        )

    # The host receipt closes the crash window after Teams settled the
    # attachment but before Python updated its database. No status check clicks.
    try:
        native = desktop.invoke(
            "teams_web_upload_status",
            {"chat": chat.model_dump(mode="json"), "request_id": request_id},
        )
    except Exception:
        native = {}
    if isinstance(native, dict) and native.get("state") == "uploaded":
        try:
            receipt = _record_upload_receipt(
                path, request_id, chat, native, expected_names=native.get("files", [])
            )
            return UploadStatus(
                status="uploaded",
                chat=chat,
                request_id=request_id,
                file_count=row[4],
                receipt=receipt,
            )
        except (TeamsError, ValueError, sqlite3.Error):
            pass
    return UploadStatus(
        status="outcome_unknown",
        chat=chat,
        request_id=request_id,
        file_count=row[4],
    )
