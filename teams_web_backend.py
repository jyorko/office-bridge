"""Read-only Teams authentication probe using an isolated persistent Edge profile."""
from __future__ import annotations

import os
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit

from robocorp import browser

TEAMS_URL = "https://teams.cloud.microsoft/"
_lock = threading.Lock()


def _attachment_denied(context) -> bool:
    """Distinguish a browser access restriction from a closed navigation tab."""
    session = None
    try:
        session = context.browser.new_browser_cdp_session()
        deadline = time.monotonic() + 10
        targets = []
        while time.monotonic() < deadline:
            targets = [target for target in session.send("Target.getTargets")["targetInfos"]
                       if target.get("type") == "page" and urlsplit(target.get("url", "")).hostname == "teams.cloud.microsoft"]
            if targets:
                break
            time.sleep(0.25)
        for target in targets:
            try:
                attached = session.send("Target.attachToTarget", {"targetId": target["targetId"], "flatten": True})
            except Exception as exc:
                if "(Target.attachToTarget): Not allowed" in str(exc):
                    return True
                raise
            session.send("Target.detachFromTarget", {"sessionId": attached["sessionId"]})
    except Exception:
        return False
    finally:
        if session is not None:
            try:
                session.detach()
            except Exception:
                pass
    return False


def _visible(page, selector: str) -> bool:
    try:
        return page.locator(selector).first.is_visible(timeout=250)
    except Exception:
        return False


def _classify(page, host: str) -> str:
    title = page.title().lower()[:200]
    if host.endswith("login.microsoftonline.com") or "sign in" in title:
        return "needs_interactive_login"
    if host != "teams.cloud.microsoft":
        return "access_blocked"

    # A loaded shell alone is not proof of a signed-in Teams session. Require
    # a recognizable application surface; identify login and access errors first.
    if _visible(page, '[data-tid="unauthorized-page"], [data-tid="error-page"]'):
        return "access_blocked"
    if _visible(page, '[data-tid="app-layout"], [data-tid="app-bar"], [data-tid="chat-list"]'):
        return "authenticated"
    if _visible(page, 'input[type="email"], input[name="loginfmt"], [data-testid="loginForm"]'):
        return "needs_interactive_login"
    if _visible(page, '[role="progressbar"], [data-tid*="skeleton"], [aria-busy="true"]'):
        return "failed"
    return "failed"


def status() -> dict[str, str]:
    """Open fixed Teams URL and return a bounded, coarse authentication result."""
    if not _lock.acquire(timeout=3):
        return {"state": "failed", "hostname": "teams.cloud.microsoft", "path": "/"}
    lock_file = None
    file_lock_acquired = False
    try:
        profile = Path(os.environ["LOCALAPPDATA"]) / "OfficeActionServer" / "teams-web-profile"
        profile.mkdir(parents=True, exist_ok=True)
        lock_file = (profile.parent / "teams-web-profile.lock").open("a+b")
        # Serialize independent Action Server worker processes using a byte lock.
        import msvcrt

        deadline = time.monotonic() + 3
        while True:
            try:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                file_lock_acquired = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    return {"state": "failed", "hostname": "teams.cloud.microsoft", "path": "/"}
                time.sleep(0.1)

        browser.configure(
            browser_engine="msedge",
            headless=True,
            persistent_context_directory=str(profile),
        )
        context = None
        try:
            page = browser.page()
            context = page.context
            page.goto(TEAMS_URL, wait_until="domcontentloaded", timeout=20000)
            # Allow Teams' client shell a bounded interval to expose ready/login/error evidence.
            deadline = time.monotonic() + 6
            state = "failed"
            while time.monotonic() < deadline:
                parts = urlsplit(page.url)
                state = _classify(page, parts.hostname or "")
                if state != "failed":
                    break
                page.wait_for_timeout(250)
            parts = urlsplit(page.url)
            host, path = parts.hostname or "", parts.path or "/"
            if host not in {"teams.cloud.microsoft", "login.microsoftonline.com"}:
                host, path, state = "teams.cloud.microsoft", "/", "access_blocked"
            return {"state": state, "hostname": host, "path": path[:200]}
        except Exception:
            if context is not None and _attachment_denied(context):
                return {"state": "access_blocked", "hostname": "teams.cloud.microsoft", "path": "/", "reason": "browser_attachment_denied"}
            return {"state": "failed", "hostname": "teams.cloud.microsoft", "path": "/"}
        finally:
            if context is not None:
                context.close()
    except Exception:
        return {"state": "failed", "hostname": "teams.cloud.microsoft", "path": "/"}
    finally:
        if lock_file is not None:
            try:
                if file_lock_acquired:
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            finally:
                lock_file.close()
        _lock.release()
