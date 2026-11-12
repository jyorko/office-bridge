"""Read-only Teams web status action."""
from typing import Annotated, Literal
from actions import ActionError, Response, action
from pydantic import BaseModel, ConfigDict, Field

import teams_web_backend


class TeamsWebStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: Literal["authenticated", "needs_interactive_login", "access_blocked", "loading", "failed"]
    hostname: str
    path: str
    browser_engine: Literal["chromium", "msedge", "webview2"]
    backend: Literal["chromium", "msedge", "webview2"]
    account_verified: bool | None = None
    host_visible: bool | None = None
    control_visible: bool | None = None
    host_focused: bool | None = None
    host_owned: bool | None = None
    foreground_unchanged: bool | None = None
    foreground_interference: bool | None = None
    samples: list[dict] | None = None
    reason: Literal["browser_attachment_denied", "profile_busy", "navigation_failed", "browser_cleanup_failed", "browser_failed", "account_unverified", "host_visible", "foreground_interference", "sdk_unavailable", "timeout", "desktop_operation_failed", "windows_required", "webview2_failed", "bridge_failed", "invalid_bridge_result", "output_overflow"] | None = None


@action(is_consequential=False)
def teams_web_status(browser_engine: Literal["chromium", "msedge", "webview2"] = "webview2") -> Response[TeamsWebStatus]:
    """Check Teams authentication in an isolated background browser profile.

    Args:
        browser_engine: Background WebView2 with Windows SSO, or a diagnostic browser.
    """
    result = teams_web_backend.backend_status(browser_engine)
    result.setdefault("browser_engine", browser_engine)
    return Response(result=TeamsWebStatus.model_validate(result))


class GroupReceipt(BaseModel):
    status: Literal["observed", "already_observed"]
    chat: Literal["QA-ASSIST-VERINT-DEPLOY"]
    message_id: str
    time: str


@action(is_consequential=True)
def teams_web_send_qa_chat(body: Annotated[str, Field(min_length=1, max_length=2000)], request_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9._-]{1,128}$")]) -> Response[GroupReceipt]:
    """Send a plain-text message to QA-ASSIST-VERINT-DEPLOY exactly once per request ID.

    Args:
        body: Complete message text, sent only to the specified group chat.
        request_id: Unique request ID; uncertain outcomes cannot be retried.
    """
    import hashlib
    import json
    import os
    from pathlib import Path
    import sqlite3
    from desktop_bridge import invoke
    from install_webview2_sdk import install

    path = Path(os.environ["LOCALAPPDATA"]) / "OfficeActionServer" / "teams-web-sends" / "group.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    with sqlite3.connect(path, timeout=3) as db:
        db.execute("CREATE TABLE IF NOT EXISTS sends (id TEXT PRIMARY KEY, digest TEXT NOT NULL, receipt TEXT)")
        db.execute("BEGIN IMMEDIATE")
        old = db.execute("SELECT digest, receipt FROM sends WHERE id=?", (request_id,)).fetchone()
        if old:
            if old[0] != digest or not old[1]:
                raise ActionError("Request already attempted or conflicts; do not retry automatically.")
            return Response(result=GroupReceipt(**{**json.loads(old[1]), "status": "already_observed"}))
        db.execute("INSERT INTO sends(id,digest,receipt) VALUES (?,?,NULL)", (request_id, digest))
        db.commit()
    try:
        with teams_web_backend.profile_lock("webview2"):
            install()
            result = invoke("teams_web_send_group", {"body": body})
        if result.get("state") != "observed" or not result.get("message_id") or not result.get("time"):
            raise ValueError("unverified")
        receipt = GroupReceipt(status="observed", chat="QA-ASSIST-VERINT-DEPLOY", message_id=result["message_id"], time=result["time"])
        with sqlite3.connect(path, timeout=3) as db:
            db.execute("UPDATE sends SET receipt=? WHERE id=?", (receipt.model_dump_json(), request_id))
        return Response(result=receipt)
    except Exception:
        raise ActionError("Send outcome unknown; inspect the chat before considering any retry.") from None
