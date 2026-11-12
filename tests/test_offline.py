"""No Office calls: exercise the real boundary with fake subprocess results."""
import json
from pathlib import Path
import subprocess

import pytest
from pydantic import ValidationError

import desktop_bridge as bridge
import office_actions as api


@pytest.fixture
def fake_process(monkeypatch):
    state = {"envelope": {"version": 1, "ok": True, "result": {"items": [], "truncated": False, "scanned_count": 0}}}

    class Process:
        pid = 987654

        def __init__(self, args, **kwargs):
            state["args"] = args
            state["kwargs"] = kwargs
            state["request"] = json.loads(Path(args[args.index("-RequestFile") + 1]).read_text(encoding="utf-8"))
            output = Path(args[args.index("-ResultFile") + 1])
            output.write_text(state.get("raw", json.dumps(state["envelope"])), encoding="utf-8")

        def wait(self, timeout):
            if state.get("timeout") and not state.get("killed"):
                raise subprocess.TimeoutExpired("powershell", timeout)
            return 0

        def kill(self):
            state["killed"] = True

    monkeypatch.setattr(bridge.subprocess, "Popen", Process)
    monkeypatch.setattr(bridge.subprocess, "run", lambda args, **kwargs: state.update(termination=args))
    return state


def test_literal_query_and_argument_array(fake_process):
    query = "%' ; $(Remove-Item x); \" OR 1=1 — café"
    result = api.outlook_search_inbox(api.InboxQuery(subject_contains=query), limit=3)
    assert result.result.items == []
    assert fake_process["request"]["arguments"]["query"]["subject_contains"] == query
    assert fake_process["kwargs"]["shell"] is False
    assert fake_process["kwargs"]["creationflags"] == subprocess.CREATE_NO_WINDOW
    assert query not in " ".join(fake_process["args"])
    assert not Path(fake_process["args"][-1]).exists()


@pytest.mark.parametrize("code", ["desktop_busy", "not_found", "stale_draft", "ambiguous_chat"])
def test_structured_failures_do_not_retry(fake_process, code):
    fake_process["envelope"] = {"version": 1, "ok": False, "error": code}
    with pytest.raises(bridge.BridgeError, match=code):
        bridge.invoke("get_message", {"message": {"entry_id": "entry", "store_id": "wrong-store"}})
    assert fake_process["request"]["arguments"]["message"]["store_id"] == "wrong-store"


@pytest.mark.parametrize("operation,code", [("get_message", "timeout"), ("send_draft", "mutation_outcome_unknown"), ("create_mail_draft", "mutation_outcome_unknown"), ("send_self_message", "mutation_outcome_unknown")])
def test_timeout_terminates_only_own_tree(fake_process, operation, code):
    fake_process["timeout"] = True
    with pytest.raises(bridge.BridgeError, match=code):
        bridge.invoke(operation, {})
    assert fake_process["killed"]
    assert fake_process["termination"][-4:] == ["/PID", "987654", "/T", "/F"]


@pytest.mark.parametrize("raw,code", [("x" * (bridge.MAX_BYTES + 1), "output_overflow"), ("not-json", "invalid_bridge_result"), ('{"version":2}', "invalid_bridge_result")], ids=["overflow", "malformed", "wrong-version"])
def test_invalid_or_oversized_result(fake_process, raw, code):
    fake_process["raw"] = raw
    with pytest.raises(bridge.BridgeError, match=code):
        bridge.invoke("inbox_list", {})


def test_models_reject_executable_fields_and_bad_identity():
    with pytest.raises(ValidationError):
        api.InboxQuery.model_validate({"dasl": "anything"})
    with pytest.raises(ValidationError):
        api.MessageRef(entry_id="item", store_id="")
    with pytest.raises(ValidationError):
        api.ChatRef(snapshot_id="bad", exact_tree_label="chat", window_title="title")
    with pytest.raises(ValidationError):
        api.InboxQuery(subject_contains="x" * 501)


def test_output_contract_rejects_unexpected_fields(fake_process):
    fake_process["envelope"]["result"]["run_script"] = "danger"
    with pytest.raises(ValidationError):
        api.outlook_inbox_list()


def test_self_send_guard_failure_is_exposed_without_retry(fake_process):
    fake_process["envelope"] = {"version": 1, "ok": False, "error": "self_identity_unverified"}
    with pytest.raises(Exception, match="self_identity_unverified"):
        api.teams_send_self_message("Synthetic self test")
    assert fake_process["request"]["operation"] == "send_self_message"
    assert fake_process["request"]["arguments"] == {"body_text": "Synthetic self test"}
