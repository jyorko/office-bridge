"""Read-only Teams probe using an isolated persistent browser profile."""

from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Literal
from urllib.parse import urlsplit

from robocorp import browser

TEAMS_URL = "https://teams.cloud.microsoft/"
BrowserEngine = Literal["chromium", "msedge"]
Backend = Literal["chromium", "msedge", "webview2"]
PROFILE_NAMES: dict[str, str] = {
    "chromium": "teams-chromium-profile",
    "msedge": "teams-web-profile",
    "webview2": "teams-webview2-profile",
}
_thread_locks = {engine: threading.Lock() for engine in PROFILE_NAMES}


def profile_path(engine: Backend = "chromium") -> Path:
    """Resolve an engine to its fixed managed profile directory."""
    if engine not in PROFILE_NAMES:
        raise ValueError(f"Unsupported browser engine: {engine}")
    return Path(os.environ["LOCALAPPDATA"]) / "OfficeActionServer" / PROFILE_NAMES[engine]


@contextmanager
def profile_lock(engine: Backend = "chromium", timeout: float = 3) -> Iterator[Path]:
    """Serialize access to one profile across threads and worker processes."""
    lock = _thread_locks[engine]
    if not lock.acquire(timeout=timeout):
        raise TimeoutError("profile_busy")
    lock_file = None
    file_locked = False
    try:
        profile = profile_path(engine)
        profile.parent.mkdir(parents=True, exist_ok=True)
        lock_file = (profile.parent / f"{PROFILE_NAMES[engine]}.lock").open("a+b")
        import msvcrt

        deadline = time.monotonic() + timeout
        while True:
            try:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                file_locked = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("profile_busy")
                time.sleep(0.1)
        profile.mkdir(parents=True, exist_ok=True)
        yield profile
    finally:
        if lock_file is not None:
            try:
                if file_locked:
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            finally:
                lock_file.close()
        lock.release()


def _attachment_denied(context) -> bool:
    """Distinguish a browser access restriction from a closed navigation tab."""
    session = None
    try:
        session = context.browser.new_browser_cdp_session()
        deadline = time.monotonic() + 10
        targets = []
        while time.monotonic() < deadline:
            targets = [
                target
                for target in session.send("Target.getTargets")["targetInfos"]
                if target.get("type") == "page"
                and urlsplit(target.get("url", "")).hostname == "teams.cloud.microsoft"
            ]
            if targets:
                break
            time.sleep(0.25)
        for target in targets:
            try:
                attached = session.send(
                    "Target.attachToTarget", {"targetId": target["targetId"], "flatten": True}
                )
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
    if host == "login.microsoftonline.com":
        return "needs_interactive_login"
    title = page.title().lower()[:200]
    if "sign in" in title:
        return "needs_interactive_login"
    if host != "teams.cloud.microsoft":
        return "access_blocked"
    if _visible(page, '[data-tid="unauthorized-page"], [data-tid="error-page"]'):
        return "access_blocked"
    if _visible(page, '[data-tid="app-layout"], [data-tid="app-bar"], [data-tid="chat-list"]'):
        return "authenticated"
    if _visible(page, 'input[type="email"], input[name="loginfmt"], [data-testid="loginForm"]'):
        return "needs_interactive_login"
    return "loading"


def status(engine: BrowserEngine = "chromium") -> dict[str, str]:
    """Open the fixed Teams URL and return a bounded, coarse authentication result."""
    result = {
        "state": "failed",
        "hostname": "teams.cloud.microsoft",
        "path": "/",
        "browser_engine": engine,
    }
    try:
        with profile_lock(engine) as profile:
            browser.configure(
                browser_engine=engine,
                headless=True,
                persistent_context_directory=str(profile),
            )
            context = None
            try:
                page = browser.page()
                context = page.context
                page.goto(TEAMS_URL, wait_until="commit", timeout=20000)
                deadline = time.monotonic() + 30
                state = "loading"
                while time.monotonic() < deadline:
                    parts = urlsplit(page.url)
                    state = _classify(page, parts.hostname or "")
                    if state != "loading":
                        break
                    page.wait_for_timeout(250)
                parts = urlsplit(page.url)
                host, path = parts.hostname or "", parts.path or "/"
                if host not in {"teams.cloud.microsoft", "login.microsoftonline.com"}:
                    host, path, state = "teams.cloud.microsoft", "/", "access_blocked"
                result.update(state=state, hostname=host, path=path[:200])
            except Exception:
                result.update(state="failed", reason="navigation_failed")
                if context is not None and _attachment_denied(context):
                    result.update(state="access_blocked", reason="browser_attachment_denied")
            finally:
                if context is not None:
                    try:
                        context.close()
                    except Exception:
                        result.update(state="failed", reason="browser_cleanup_failed")
    except TimeoutError:
        result["reason"] = "profile_busy"
    except Exception:
        result.update(state="failed", reason="browser_failed")
    return result


def webview2_status() -> dict[str, object]:
    """Run the fixed native adapter behind the same per-profile lock."""
    from office_bridge.desktop import BridgeError, invoke

    result: dict[str, object] = {
        "state": "failed",
        "hostname": "teams.cloud.microsoft",
        "path": "/",
        "backend": "webview2",
    }
    try:
        native = invoke("teams_web_status", {})
        result.update(native)
        result["backend"] = "webview2"
        if native.get("state") == "authenticated":
            if native.get("account_verified") is not True:
                result.update(state="failed", reason="account_unverified")
            elif (
                native.get("host_visible") is not False
                or native.get("control_visible") is not False
            ):
                result.update(state="failed", reason="host_visible")
            elif (
                native.get("host_focused") is not False
                or native.get("foreground_interference") is not False
            ):
                result.update(state="failed", reason="foreground_interference")
    except TimeoutError:
        result.update(state="failed", reason="profile_busy")
    except BridgeError as exc:
        result.update(state="failed", reason=exc.code)
    except Exception:
        result.update(state="failed", reason="webview2_failed")
    return result


def backend_status(backend: Backend = "webview2") -> dict[str, object]:
    if backend == "webview2":
        return webview2_status()
    result = status(backend)
    result["backend"] = backend
    return result
