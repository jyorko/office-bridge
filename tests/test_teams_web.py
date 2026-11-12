from types import SimpleNamespace

from office_bridge.teams import backend


class Locator:
    def __init__(self, visible=False):
        self.visible = visible
        self.first = self

    def is_visible(self, timeout=None):
        return self.visible


class Page:
    def __init__(self, url, visible=()):
        self.url = url
        self.visible = set(visible)

    def title(self):
        return "Teams"

    def locator(self, selector):
        return Locator(selector in self.visible)


def test_classification_requires_application_ready_surface():
    assert (
        backend._classify(Page("https://teams.cloud.microsoft/"), "teams.cloud.microsoft")
        == "loading"
    )
    assert (
        backend._classify(
            Page(
                "https://teams.cloud.microsoft/",
                {'[data-tid="app-layout"], [data-tid="app-bar"], [data-tid="chat-list"]'},
            ),
            "teams.cloud.microsoft",
        )
        == "authenticated"
    )


def test_classification_handles_login_and_access_block():
    assert (
        backend._classify(Page("https://login.microsoftonline.com/"), "login.microsoftonline.com")
        == "needs_interactive_login"
    )
    assert (
        backend._classify(
            Page(
                "https://teams.cloud.microsoft/",
                {'[data-tid="unauthorized-page"], [data-tid="error-page"]'},
            ),
            "teams.cloud.microsoft",
        )
        == "access_blocked"
    )


def test_status_uses_supported_browser_configuration_and_releases_lock(tmp_path, monkeypatch):
    profile_root = tmp_path / "OfficeActionServer"
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    page = Page(
        "https://teams.cloud.microsoft/",
        {'[data-tid="app-layout"], [data-tid="app-bar"], [data-tid="chat-list"]'},
    )
    calls = []
    monkeypatch.setattr(
        backend.browser, "configure", lambda **kwargs: calls.append(("configure", kwargs))
    )
    monkeypatch.setattr(backend.browser, "page", lambda: page)
    page.context = SimpleNamespace(close=lambda: calls.append(("close", {})))
    monkeypatch.setattr(
        page, "goto", lambda *args, **kwargs: calls.append(("goto", (args, kwargs))), raising=False
    )
    monkeypatch.setattr(page, "wait_for_timeout", lambda ms: None, raising=False)

    assert backend.status()["state"] == "authenticated"
    assert calls[0] == (
        "configure",
        {
            "browser_engine": "chromium",
            "headless": True,
            "persistent_context_directory": str(profile_root / "teams-chromium-profile"),
        },
    )
    assert backend.status("msedge")["browser_engine"] == "msedge"
    assert calls[-3][1]["persistent_context_directory"] == str(profile_root / "teams-web-profile")
    assert calls[-1][0] == "close"
    assert backend.status()["state"] == "authenticated"


def test_profile_mapping_is_fixed_and_engine_specific(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert (
        backend.profile_path("chromium")
        == tmp_path / "OfficeActionServer" / "teams-chromium-profile"
    )
    assert backend.profile_path("msedge") == tmp_path / "OfficeActionServer" / "teams-web-profile"
    try:
        backend.profile_path("arbitrary")
    except ValueError:
        pass
    else:
        raise AssertionError("arbitrary profile engine accepted")


def test_failed_cleanup_cannot_report_authenticated(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    page = Page(
        "https://teams.cloud.microsoft/",
        {'[data-tid="app-layout"], [data-tid="app-bar"], [data-tid="chat-list"]'},
    )

    def close():
        raise RuntimeError("browser did not close")

    page.context = SimpleNamespace(close=close)
    page.goto = lambda *args, **kwargs: None
    monkeypatch.setattr(backend.browser, "configure", lambda **kwargs: None)
    monkeypatch.setattr(backend.browser, "page", lambda: page)
    result = backend.status()
    assert result["state"] == "failed"
    assert result["reason"] == "browser_cleanup_failed"
    with backend.profile_lock(timeout=0.1):
        pass


def test_profile_busy_is_reported_and_lock_can_be_released(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    lock = backend._thread_locks["chromium"]
    assert lock.acquire(blocking=False)
    try:
        result = backend.status()
    finally:
        lock.release()
    assert result["reason"] == "profile_busy"
    with backend.profile_lock("chromium", timeout=0.1) as profile:
        assert profile.name == "teams-chromium-profile"


def test_status_can_explicitly_select_edge(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    calls = []
    monkeypatch.setattr(backend.browser, "configure", lambda **kwargs: calls.append(kwargs))
    page = Page("https://login.microsoftonline.com/")
    page.context = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(backend.browser, "page", lambda: page)
    monkeypatch.setattr(page, "goto", lambda *args, **kwargs: None, raising=False)
    assert backend.status("msedge")["state"] == "needs_interactive_login"
    assert calls[0]["browser_engine"] == "msedge"
    assert calls[0]["persistent_context_directory"].endswith("teams-web-profile")


def test_browser_attachment_denial_is_reported():
    calls = []

    class Session:
        def send(self, method, params=None):
            if method == "Target.getTargets":
                return {
                    "targetInfos": [
                        {
                            "type": "page",
                            "url": "https://teams.cloud.microsoft/",
                            "targetId": "teams",
                        }
                    ]
                }
            raise RuntimeError(
                "CDPSession.send: Protocol error (Target.attachToTarget): Not allowed"
            )

        def detach(self):
            calls.append("detached")

    context = SimpleNamespace(browser=SimpleNamespace(new_browser_cdp_session=lambda: Session()))
    assert backend._attachment_denied(context)
    assert calls == ["detached"]


def test_status_preserves_edge_attachment_denial_diagnosis(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    class Session:
        def send(self, method, params=None):
            if method == "Target.getTargets":
                return {
                    "targetInfos": [
                        {
                            "type": "page",
                            "url": "https://teams.cloud.microsoft/",
                            "targetId": "teams",
                        }
                    ]
                }
            raise RuntimeError(
                "CDPSession.send: Protocol error (Target.attachToTarget): Not allowed"
            )

        def detach(self):
            pass

    context = SimpleNamespace(
        browser=SimpleNamespace(new_browser_cdp_session=lambda: Session()),
        close=lambda: None,
    )
    page = Page("https://teams.cloud.microsoft/")
    page.context = context
    monkeypatch.setattr(backend.browser, "configure", lambda **kwargs: None)
    monkeypatch.setattr(backend.browser, "page", lambda: page)
    monkeypatch.setattr(
        page,
        "goto",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("navigation detached")),
        raising=False,
    )
    result = backend.status("msedge")
    assert result["state"] == "access_blocked"
    assert result["browser_engine"] == "msedge"
    assert result["reason"] == "browser_attachment_denied"


def test_webview2_status_routes_through_persistent_session_bridge(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    from office_bridge import desktop

    calls = []

    def invoke(operation, arguments):
        calls.append((operation, arguments))
        return {
            "state": "needs_interactive_login",
            "hostname": "login.microsoftonline.com",
            "path": "/",
            "backend": "webview2",
            "account_verified": False,
            "host_visible": False,
            "control_visible": False,
            "host_focused": False,
            "host_owned": True,
            "foreground_unchanged": True,
        }

    monkeypatch.setattr(desktop, "invoke", invoke)
    result = backend.backend_status("webview2")
    assert result["backend"] == "webview2"
    assert result["state"] == "needs_interactive_login"
    assert calls == [("teams_web_status", {})]
    with backend.profile_lock("webview2", timeout=0.1) as profile:
        assert profile == tmp_path / "OfficeActionServer" / "teams-webview2-profile"


def test_webview2_never_reports_unverified_or_interfering_authentication(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    from office_bridge import desktop

    monkeypatch.setattr(
        desktop,
        "invoke",
        lambda *_: {
            "state": "authenticated",
            "hostname": "teams.cloud.microsoft",
            "path": "/",
            "account_verified": False,
            "host_visible": False,
            "control_visible": False,
            "host_focused": False,
            "foreground_unchanged": True,
        },
    )
    result = backend.backend_status("webview2")
    assert result["state"] == "failed"
    assert result["reason"] == "account_unverified"

    monkeypatch.setattr(
        desktop,
        "invoke",
        lambda *_: {
            "state": "authenticated",
            "hostname": "teams.cloud.microsoft",
            "path": "/",
            "account_verified": True,
            "host_visible": True,
            "control_visible": False,
            "host_focused": False,
            "foreground_unchanged": True,
        },
    )
    result = backend.backend_status("webview2")
    assert result["state"] == "failed"
    assert result["reason"] == "host_visible"


def test_webview2_distinguishes_user_window_switches_from_owned_focus(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    from office_bridge import desktop

    result = {
        "state": "authenticated",
        "hostname": "teams.cloud.microsoft",
        "path": "/",
        "account_verified": True,
        "host_visible": False,
        "control_visible": False,
        "host_focused": False,
        "foreground_interference": False,
        "foreground_unchanged": False,
    }
    monkeypatch.setattr(desktop, "invoke", lambda *_: dict(result))
    assert backend.backend_status("webview2")["state"] == "authenticated"
    result["foreground_interference"] = True
    assert backend.backend_status("webview2")["state"] == "failed"
