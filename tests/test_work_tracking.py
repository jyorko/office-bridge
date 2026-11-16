import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from office_bridge.teams import work_tracking as tracking
from office_bridge.teams.models import WebChatRef
from office_bridge.teams.service import TeamsError

CHAT = WebChatRef(chat_id="19:" + "a" * 32 + "@thread.v2", title="Example")
MESSAGE = "message-body-1700000000000"


@pytest.fixture
def ui(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("OFFICE_BRIDGE_ACCOUNT", raising=False)
    config = tmp_path / "OfficeActionServer/config"
    config.mkdir(parents=True)
    (config / "settings.json").write_text(json.dumps({"teams_account": "agent@example.invalid"}))
    state = {"selected": False, "available": True, "content": "Task", "writes": []}

    def observe(*_):
        return {
            "content_text": state["content"],
            "reactions": (
                [{"name": "Eyes", "emoji_id": "1f440_eyes", "selected": state["selected"]}]
                if state["available"]
                else []
            ),
        }

    def set_eyes(chat, message, content, selected, request):
        state["writes"].append((selected, request))
        assert chat == CHAT and message == MESSAGE and content == state["content"]
        if state["selected"] == selected:
            return "already_reacted" if selected else "already_removed"
        state["selected"] = selected
        return "reacted" if selected else "removed"

    monkeypatch.setattr(tracking.messages, "observe", observe)
    monkeypatch.setattr(tracking.messages, "set_eyes", set_eyes)
    return state


def enroll(session="session"):
    return tracking.configure(session, [CHAT], True)


def begin(work="one", session="session"):
    return tracking.begin(session, work, CHAT, MESSAGE, "Task")


def finish(work="one", session="session", outcome="completed"):
    return tracking.finish(session, work, CHAT, MESSAGE, outcome)


def test_tracking_requires_explicit_exact_scope(ui):
    with pytest.raises(TeamsError, match="opted in"):
        begin()
    enroll()
    wrong = CHAT.model_copy(update={"title": "Different"})
    with pytest.raises(TeamsError, match="opted in"):
        tracking.begin("session", "one", wrong, MESSAGE, "Task")
    assert ui["writes"] == []


def test_shared_claims_only_last_finisher_cleans_owned_eyes(ui):
    enroll()
    enroll("second-session")
    assert begin().marker == "owned"
    assert begin("two", "second-session").active_claims == 2
    assert begin().active_claims == 2
    assert finish().active_claims == 1
    assert ui["selected"]
    assert finish("two", "second-session").marker == "cleared"
    assert not ui["selected"]
    assert [desired for desired, _ in ui["writes"]] == [True, False]
    assert len({request for _, request in ui["writes"]}) == 2
    assert finish("two", "second-session").marker == "cleared"
    assert begin("two", "second-session").active_claims == 0
    assert len(ui["writes"]) == 2


def test_preexisting_eyes_is_never_claimed_or_removed(ui):
    ui["selected"] = True
    enroll()
    assert begin().marker == "preexisting"
    assert finish().marker == "preexisting"
    assert ui["selected"] and ui["writes"] == []


def test_no_click_add_race_does_not_create_ownership(ui, monkeypatch):
    enroll()
    monkeypatch.setattr(tracking.messages, "set_eyes", lambda *_: "already_reacted")
    assert begin().marker == "preexisting"
    assert finish().marker == "preexisting"
    assert ui["writes"] == []


@pytest.mark.parametrize("outcome", ["completed", "paused", "failed", "cancelled"])
def test_every_end_state_clears_only_owned_eyes(ui, outcome):
    enroll()
    begin()
    result = finish(outcome=outcome)
    assert result.claims[0].outcome == outcome
    assert result.marker == "cleared"
    assert [desired for desired, _ in ui["writes"]] == [True, False]


def test_disable_blocks_new_claims_but_keeps_cleanup_authorization(ui):
    enroll()
    begin()
    tracking.configure("session", [CHAT], False)
    with pytest.raises(TeamsError, match="opted in"):
        begin("two")
    assert finish().marker == "cleared"


def test_missing_eyes_tracks_work_without_substitution(ui):
    ui["available"] = False
    enroll()
    result = begin()
    assert result.marker == "unmarked" and result.warning
    assert finish().active_claims == 0
    assert ui["writes"] == []


@pytest.mark.parametrize("change", ["content", "selected"])
def test_external_changes_stop_automatic_cleanup(ui, change):
    enroll()
    begin()
    ui[change] = "Edited" if change == "content" else False
    assert finish().marker == "interference"
    ui["selected"] = True  # A later manual re-add must remain untouched.
    assert begin("new").marker == "interference"
    assert finish().marker == "interference"
    assert len(ui["writes"]) == 1


def test_uncertain_add_blocks_every_new_claim_id_and_cleanup(ui, monkeypatch):
    enroll()

    def unknown(*_):
        ui["writes"].append("uncertain")
        raise TeamsError("Outcome unknown")

    monkeypatch.setattr(tracking.messages, "set_eyes", unknown)
    assert begin().marker == "uncertain"
    assert begin("new").marker == "uncertain"
    assert finish().marker == "uncertain"
    assert tracking.status(CHAT, MESSAGE).marker == "uncertain"
    assert ui["writes"] == ["uncertain"]


def test_crash_during_click_persists_pending_across_workers(ui, monkeypatch):
    enroll()

    def crash(*_):
        raise KeyboardInterrupt

    monkeypatch.setattr(tracking.messages, "set_eyes", crash)
    with pytest.raises(KeyboardInterrupt):
        begin()
    assert tracking.status(CHAT, MESSAGE).marker == "pending"
    assert begin("different-worker").marker == "pending"
    assert finish().marker == "pending"


def test_crash_before_cleanup_can_reconcile_without_replaying_transition(ui, monkeypatch):
    enroll()
    begin()
    observe = tracking.messages.observe
    monkeypatch.setattr(
        tracking.messages, "observe", lambda *_: (_ for _ in ()).throw(KeyboardInterrupt())
    )
    with pytest.raises(KeyboardInterrupt):
        finish()
    monkeypatch.setattr(tracking.messages, "observe", observe)
    assert finish().marker == "cleared"
    assert [desired for desired, _ in ui["writes"]] == [True, False]


def test_uncertain_removal_blocks_new_request_ids(ui, monkeypatch):
    enroll()
    begin()
    monkeypatch.setattr(
        tracking.messages, "set_eyes", lambda *_: (_ for _ in ()).throw(TeamsError("Unknown"))
    )
    assert finish().marker == "uncertain"
    assert finish().marker == "uncertain"
    assert begin("new").marker == "uncertain"
    assert len(ui["writes"]) == 1


def test_concurrent_agents_share_one_owned_marker(ui):
    enroll()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(begin, ["first", "second"]))
    assert sorted(r.active_claims for r in results) == [1, 2]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(finish, ["first", "second"]))
    assert any(r.marker == "cleared" for r in results)
    assert [desired for desired, _ in ui["writes"]] == [True, False]


def test_scope_and_terminal_outcome_cannot_be_rewritten(ui):
    enroll()
    with pytest.raises(TeamsError, match="new session"):
        tracking.configure("session", [], False)
    begin()
    finish(outcome="cancelled")
    with pytest.raises(TeamsError, match="different outcome"):
        finish(outcome="completed")


def test_read_only_status_detects_interference_without_mutation(ui):
    enroll()
    begin()
    ui["selected"] = False
    assert tracking.status(CHAT, MESSAGE).marker == "interference"
    assert len(ui["writes"]) == 1
