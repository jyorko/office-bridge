from types import SimpleNamespace

import teams_web_backend as backend


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
    assert backend._classify(Page("https://teams.cloud.microsoft/"), "teams.cloud.microsoft") == "failed"
    assert backend._classify(
        Page("https://teams.cloud.microsoft/", {'[data-tid="app-layout"], [data-tid="app-bar"], [data-tid="chat-list"]'}),
        "teams.cloud.microsoft",
    ) == "authenticated"


def test_classification_handles_login_and_access_block():
    assert backend._classify(Page("https://login.microsoftonline.com/"), "login.microsoftonline.com") == "needs_interactive_login"
    assert backend._classify(
        Page("https://teams.cloud.microsoft/", {'[data-tid="unauthorized-page"], [data-tid="error-page"]'}),
        "teams.cloud.microsoft",
    ) == "access_blocked"


def test_status_uses_supported_browser_configuration_and_releases_lock(tmp_path, monkeypatch):
    profile_root = tmp_path / "OfficeActionServer"
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    page = Page("https://teams.cloud.microsoft/", {'[data-tid="app-layout"], [data-tid="app-bar"], [data-tid="chat-list"]'})
    calls = []
    monkeypatch.setattr(backend.browser, "configure", lambda **kwargs: calls.append(("configure", kwargs)))
    monkeypatch.setattr(backend.browser, "page", lambda: page)
    page.context = SimpleNamespace(close=lambda: calls.append(("close", {})))
    monkeypatch.setattr(page, "goto", lambda *args, **kwargs: calls.append(("goto", (args, kwargs))), raising=False)
    monkeypatch.setattr(page, "wait_for_timeout", lambda ms: None, raising=False)

    assert backend.status()["state"] == "authenticated"
    assert calls[0] == ("configure", {
        "browser_engine": "msedge",
        "headless": True,
        "persistent_context_directory": str(profile_root / "teams-web-profile"),
    })
    assert calls[-1][0] == "close"
    assert backend.status()["state"] == "authenticated"
