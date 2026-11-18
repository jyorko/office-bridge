import sqlite3

import pytest

from office_bridge import window_view_cache as cache
from office_bridge.outlook.models import MessageRef
from office_bridge.presentation import ReadMail, View, present


@pytest.fixture
def storage(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("OFFICE_CANVAS_SIGNING_KEY", "window-fixture-key")
    return tmp_path / "OfficeActionServer/config/window-views.sqlite3"


def display():
    value = present(View(source="Outlook", title="Inbox", cards=[{"heading": "Agenda"}]))
    value.mail = ReadMail(rows=[MessageRef(entry_id="fixture", store_id="fixture-store")])
    value.window_proof = value.proof()
    cache.retain(value)
    return value


def test_existing_view_survives_a_new_database_connection(storage):
    existing = display()
    opened = cache.resolve(existing.window, existing.view_id)
    assert opened.html == existing.html
    assert opened.mail == existing.mail
    assert opened.window_proof == existing.window_proof
    assert "Agenda" not in existing.window
    with pytest.raises(ValueError, match="match"):
        cache.resolve("Different window", existing.view_id)


def test_altered_expired_and_other_session_views_are_rejected(storage, monkeypatch):
    existing = display()
    with sqlite3.connect(storage) as db:
        db.execute("UPDATE views SET payload = replace(payload, 'Agenda', 'Altered')")
    with pytest.raises(ValueError, match="verified"):
        cache.resolve(existing.window, existing.view_id)
    existing = display()
    real_time = cache.time.time
    monkeypatch.setattr(cache.time, "time", lambda: real_time() + cache.TTL + 1)
    with pytest.raises(ValueError, match="expired"):
        cache.resolve(existing.window, existing.view_id)
    monkeypatch.setenv("OFFICE_CANVAS_SIGNING_KEY", "another-server-session")
    with pytest.raises(ValueError, match="available"):
        cache.resolve(existing.window, existing.view_id)


def test_private_cache_prunes_old_views_and_caps_payloads(storage, monkeypatch):
    monkeypatch.setattr(cache, "MAX_VIEWS", 2)
    first, second, third = display(), display(), display()
    with pytest.raises(ValueError, match="available"):
        cache.resolve(first.window, first.view_id)
    assert cache.resolve(second.window, second.view_id)
    assert cache.resolve(third.window, third.view_id)
    monkeypatch.setattr(cache, "MAX_VIEW_BYTES", 2)
    with pytest.raises(ValueError, match="size limit"):
        display()
