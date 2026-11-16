"""Bounded state exploration against Office Bridge's real durable write guards.

This is a local verifier, not a live Teams test. It imports the production Python
functions and substitutes only the external Teams/native bridge calls. All ledgers
and configuration are created under temporary directories.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import itertools
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import types
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = REPO / "src"
CHAT_ID = "19:bounded-check@thread.v2"
CHAT_TITLE = "Verification fixture"
MESSAGE_ID = "message-body-1700000000000"
CONTENT = "bounded work item"
ACCOUNT = "verifier@example.invalid"


class SimulatedProcessLoss(BaseException):
    """Bypass normal exception recovery to model a process disappearing."""


def _module_source(source_root: Path) -> None:
    # The Actions Runtime package is not needed by the exercised pure Python
    # ledger paths. Its public boundary classes are imported by the desktop
    # adapter at module import time, so supply test-only inert stand-ins when the
    # managed Action Server environment is absent.
    if importlib.util.find_spec("actions") is None:

        class ActionError(RuntimeError):
            pass

        class Response:
            def __init__(self, result):
                self.result = result

        sys.modules["actions"] = types.SimpleNamespace(ActionError=ActionError, Response=Response)
    sys.path.insert(0, str(source_root.resolve()))
    for name in tuple(sys.modules):
        if name == "office_bridge" or name.startswith("office_bridge."):
            del sys.modules[name]
    importlib.invalidate_caches()


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _record_sources() -> dict[str, str]:
    names = (
        "office_bridge/teams/work_tracking.py",
        "office_bridge/teams/messages.py",
        "office_bridge/teams/ledger.py",
        "office_bridge/teams/service.py",
        "office_bridge/teams/copilot.py",
    )
    return {name: _hash(DEFAULT_SOURCE / name) for name in names}


def _isolated_localapp(stack: ExitStack) -> Path:
    root = Path(tempfile.mkdtemp(prefix="office-bridge-safety-"))
    stack.callback(shutil.rmtree, root, ignore_errors=True)
    stack.enter_context(
        patch.dict(
            os.environ,
            {"LOCALAPPDATA": str(root), "OFFICE_BRIDGE_ACCOUNT": ACCOUNT},
            clear=False,
        )
    )
    (root / "OfficeActionServer" / "config").mkdir(parents=True)
    (root / "OfficeActionServer" / "config" / "settings.json").write_text(
        json.dumps({"teams_account": ACCOUNT}), encoding="utf-8"
    )
    return root


def _read_marker(root: Path, key: str) -> tuple[str | None, int, list[tuple[str, str]]]:
    db_path = (
        root
        / "OfficeActionServer"
        / "teams-web-sends"
        / "work-tracking"
        / hashlib.sha256(ACCOUNT.encode()).hexdigest()
        / f"{key}.sqlite3"
    )
    if not db_path.exists():
        return None, 0, []
    with sqlite3.connect(db_path) as db:
        marker = db.execute("SELECT state,generation FROM marker WHERE id=1").fetchone()
        claims = db.execute("SELECT work,outcome FROM claims ORDER BY work").fetchall()
    return marker[0] if marker else None, marker[1] if marker else 0, claims


def _event_orders() -> list[tuple[str, ...]]:
    events = ("begin-a", "begin-b", "finish-a", "finish-b")
    valid = []
    for order in itertools.permutations(events):
        positions = {event: order.index(event) for event in events}
        if (
            positions["begin-a"] < positions["finish-a"]
            and positions["begin-b"] < positions["finish-b"]
        ):
            valid.append(order)
    return valid


def explore_work_tracking() -> int:
    from office_bridge.teams import work_tracking
    from office_bridge.teams.models import WebChatRef
    from office_bridge.teams.service import TeamsError

    chat = WebChatRef(chat_id=CHAT_ID, title=CHAT_TITLE)
    cases = 0
    orders = _event_orders()

    branches = (
        (False, "reacted", ("removed", "already_removed", "uncertain")),
        (False, "already_reacted", ("removed",)),
        (False, "uncertain", ("removed",)),
        (True, "reacted", ("removed",)),
        (None, "reacted", ("removed",)),
    )
    scenarios = (
        (initial, add_result, remove_result, order)
        for initial, add_result, removals in branches
        for remove_result in removals
        for order in orders
    )
    for initial, add_result, remove_result, order in scenarios:
        with ExitStack() as stack:
            root = _isolated_localapp(stack)
            work_tracking.configure("session-a", [chat], True)
            work_tracking.configure("session-b", [chat], True)
            remote = {"selected": initial, "content": CONTENT}
            clicks: list[bool] = []
            key = hashlib.sha256(json.dumps([CHAT_ID, MESSAGE_ID]).encode()).hexdigest()

            def observe(_chat, _message_id):
                reactions = (
                    [{"emoji_id": "1f440_eyes", "selected": remote["selected"]}]
                    if remote["selected"] is not None
                    else []
                )
                return {"content_text": remote["content"], "reactions": reactions}

            def set_eyes(_chat, _message_id, _content, selected, _request_id):
                marker, _generation, claims = _read_marker(root, key)
                active = sum(outcome == "active" for _, outcome in claims)
                assert marker == "pending", f"native call did not follow durable pending: {marker}"
                assert (
                    active > 0 if selected else active == 0
                ), f"invalid active-claim count {active} for selected={selected}"
                clicks.append(selected)
                outcome = add_result if selected else remove_result
                if outcome == "uncertain":
                    raise TeamsError("injected uncertain native result")
                remote["selected"] = selected
                return outcome

            stack.enter_context(patch.object(work_tracking.messages, "observe", observe))
            stack.enter_context(patch.object(work_tracking.messages, "set_eyes", set_eyes))

            started: set[str] = set()
            for event in order:
                if event.startswith("begin"):
                    letter = event[-1]
                    session = "session-a" if letter == "a" else "session-b"
                    result = work_tracking.begin(
                        session, f"work-{letter}", chat, MESSAGE_ID, CONTENT
                    )
                    if f"work-{letter}" in {work.work_id for work in result.claims}:
                        started.add(letter)
                    # An uncertain transition is sticky: no later work ID may claim
                    # the message while the transition has no settled result.
                    if result.marker in {"pending", "uncertain", "interference"}:
                        assert all(work.work_id != "work-new" for work in result.claims)
                else:
                    letter = event[-1]
                    if letter not in started:
                        continue
                    session = "session-a" if letter == "a" else "session-b"
                    work_tracking.finish(session, f"work-{letter}", chat, MESSAGE_ID, "completed")

            state, generation, claims = _read_marker(root, key)
            assert generation >= 0
            assert len(claims) <= 2
            assert sum(selected for selected in clicks) <= generation
            if initial is True or initial is None:
                assert (
                    False not in clicks
                ), "the coordinator removed Eyes that it did not establish ownership for"
            if add_result == "already_reacted":
                assert (
                    False not in clicks
                ), "a no-click add response was incorrectly treated as owned Eyes"
            # An injected uncertain result is durable and prevents later automatic
            # transitions, even though the remaining bounded event order continues.
            if add_result == "uncertain" and initial is False:
                assert state in {"uncertain", "pending"}
                assert False not in clicks
            if remove_result == "uncertain" and state == "uncertain":
                assert clicks.count(False) <= 1
            cases += 1
    return cases


def check_pending_crashes() -> int:
    from office_bridge.teams import work_tracking
    from office_bridge.teams.models import WebChatRef

    chat = WebChatRef(chat_id=CHAT_ID, title=CHAT_TITLE)
    checks = 0
    for direction, crash_point in itertools.product(("add", "remove"), ("before", "after")):
        with ExitStack() as stack:
            root = _isolated_localapp(stack)
            work_tracking.configure("session-a", [chat], True)
            remote = {"selected": False, "content": CONTENT}
            calls: list[bool] = []
            key = hashlib.sha256(json.dumps([CHAT_ID, MESSAGE_ID]).encode()).hexdigest()

            def observe(_chat, _message_id):
                return {
                    "content_text": remote["content"],
                    "reactions": [{"emoji_id": "1f440_eyes", "selected": remote["selected"]}],
                }

            def set_eyes(_chat, _message_id, _content, selected, _request_id):
                calls.append(selected)
                if direction == "add":
                    if not selected:
                        raise AssertionError("unexpected removal during add crash case")
                elif selected:
                    remote["selected"] = True
                    return "reacted"

                if crash_point == "after":
                    remote["selected"] = selected
                raise SimulatedProcessLoss()

            stack.enter_context(patch.object(work_tracking.messages, "observe", observe))
            stack.enter_context(patch.object(work_tracking.messages, "set_eyes", set_eyes))

            if direction == "add":
                try:
                    work_tracking.begin("session-a", "work-a", chat, MESSAGE_ID, CONTENT)
                except SimulatedProcessLoss:
                    pass
                else:
                    raise AssertionError("add transition did not simulate process loss")
                expected_calls = [True]
            else:
                work_tracking.begin("session-a", "work-a", chat, MESSAGE_ID, CONTENT)
                try:
                    work_tracking.finish("session-a", "work-a", chat, MESSAGE_ID, "completed")
                except SimulatedProcessLoss:
                    pass
                else:
                    raise AssertionError("remove transition did not simulate process loss")
                expected_calls = [True, False]

            state, _generation, claims = _read_marker(root, key)
            assert state == "pending"
            assert calls == expected_calls
            retry = work_tracking.begin("session-a", "work-new", chat, MESSAGE_ID, CONTENT)
            assert "work-new" not in {work.work_id for work in retry.claims}
            work_tracking.begin("session-a", "work-a", chat, MESSAGE_ID, CONTENT)
            work_tracking.finish("session-a", "work-a", chat, MESSAGE_ID, "completed")
            assert calls == expected_calls, f"pending {direction} was automatically retried"
            checks += 1
    return checks


def check_send_ledger() -> int:
    from office_bridge.teams import ledger

    checks = 0
    with ExitStack() as stack:
        root = _isolated_localapp(stack)
        stack.enter_context(patch.dict(os.environ, {"OFFICE_BRIDGE_ACCOUNT": ACCOUNT}))
        stack.enter_context(
            patch.object(ledger, "LEDGER_PATH", root / "OfficeActionServer" / "ledger.json")
        )
        submit_calls = 0

        def uncertain_submit():
            nonlocal submit_calls
            submit_calls += 1
            raise OSError("connection dropped after click")

        try:
            ledger.send_once("request-a", "private body", uncertain_submit)
        except ledger.SendLedgerError as error:
            assert error.code == "send_outcome_unknown"
        else:
            raise AssertionError("uncertain send was accepted")
        try:
            ledger.send_once("request-a", "private body", uncertain_submit)
        except ledger.SendLedgerError as error:
            assert error.code == "send_outcome_unknown"
        else:
            raise AssertionError("uncertain send was retried")
        assert submit_calls == 1
        checks += 1

    with ExitStack() as stack:
        root = _isolated_localapp(stack)
        stack.enter_context(patch.dict(os.environ, {"OFFICE_BRIDGE_ACCOUNT": ACCOUNT}))
        path = root / "OfficeActionServer" / "ledger.json"
        stack.enter_context(patch.object(ledger, "LEDGER_PATH", path))
        original_write = ledger._write

        def crash_after_reservation(write_path, data):
            original_write(write_path, data)
            raise SimulatedProcessLoss()

        stack.enter_context(patch.object(ledger, "_write", crash_after_reservation))
        submit_calls = 0

        def submit():
            nonlocal submit_calls
            submit_calls += 1
            return {}

        try:
            ledger.send_once("request-before-click", "body", submit)
        except SimulatedProcessLoss:
            pass
        else:
            raise AssertionError("did not simulate process loss after send reservation")
        assert submit_calls == 0
        with patch.object(ledger, "_write", original_write):
            try:
                ledger.send_once("request-before-click", "body", submit)
            except ledger.SendLedgerError as error:
                assert error.code == "send_outcome_unknown"
            else:
                raise AssertionError("reserved send was automatically retried")
        assert submit_calls == 0
        checks += 1
    return checks


def check_edit_reservation() -> int:
    from office_bridge.teams import service
    from office_bridge.teams.models import WebChatRef
    from office_bridge.teams.service import TeamsError

    with ExitStack() as stack:
        _isolated_localapp(stack)
        chat = WebChatRef(chat_id=CHAT_ID, title=CHAT_TITLE)
        invokes: list[str] = []

        def invoke(operation, _arguments):
            invokes.append(operation)
            if operation == "teams_web_inspect_message":
                return {
                    "state": "inspected",
                    "chat_id": CHAT_ID,
                    "title": CHAT_TITLE,
                    "message_id": MESSAGE_ID,
                    "own_message": True,
                    "text": "old text",
                }
            if operation == "teams_web_edit_message":
                raise SimulatedProcessLoss()
            raise AssertionError(operation)

        stack.enter_context(patch.object(service.ledger, "has_request_id", lambda _id: False))
        stack.enter_context(patch.object(service.desktop, "invoke", invoke))
        try:
            service.edit_chat(chat, MESSAGE_ID, "old text", "new text", "edit-a")
        except SimulatedProcessLoss:
            pass
        else:
            raise AssertionError("did not simulate process loss after edit reservation")

        before_retry = invokes.count("teams_web_edit_message")
        try:
            service.edit_chat(chat, MESSAGE_ID, "old text", "new text", "edit-a")
        except TeamsError:
            pass
        else:
            raise AssertionError("reserved edit was automatically retried")
        assert invokes.count("teams_web_edit_message") == before_retry == 1
    return 1


def check_copilot_reservation() -> int:
    from office_bridge.teams import copilot
    from office_bridge.teams.copilot import CopilotError

    with ExitStack() as stack:
        _isolated_localapp(stack)
        invokes: list[str] = []

        def invoke(operation, _arguments):
            invokes.append(operation)
            if operation == "teams_copilot_status":
                return {
                    "state": "ready",
                    "work_iq_enabled": True,
                    "composer_nonempty": False,
                }
            if operation == "teams_copilot_ask":
                raise SimulatedProcessLoss()
            raise AssertionError(operation)

        stack.enter_context(patch.object(copilot, "_invoke", invoke))
        try:
            copilot.ask("bounded question", "copilot-a")
        except SimulatedProcessLoss:
            pass
        else:
            raise AssertionError("did not simulate process loss after Copilot reservation")

        before_retry = invokes.count("teams_copilot_ask")
        try:
            copilot.ask("bounded question", "copilot-a")
        except CopilotError:
            pass
        else:
            raise AssertionError("reserved Copilot question was automatically resubmitted")
        assert invokes.count("teams_copilot_ask") == before_retry == 1
    return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source-root",
        type=Path,
        default=DEFAULT_SOURCE,
        help="alternate source tree, used only for scratch mutation checks",
    )
    parser.add_argument("--print-source-hashes", action="store_true")
    args = parser.parse_args()
    if args.print_source_hashes:
        print(json.dumps(_record_sources(), indent=2, sort_keys=True))
        return 0
    if args.source_root.resolve() == DEFAULT_SOURCE.resolve():
        recorded_path = REPO / "scripts" / "verification" / "source-hashes.json"
        recorded = json.loads(recorded_path.read_text(encoding="utf-8"))
        current = _record_sources()
        if current != recorded:
            print("STALE coverage hashes: review changed transaction boundaries before rerunning.")
            print(json.dumps({"recorded": recorded, "current": current}, indent=2, sort_keys=True))
            return 2
    _module_source(args.source_root)
    work_cases = explore_work_tracking()
    crash_checks = check_pending_crashes()
    send_checks = check_send_ledger()
    edit_checks = check_edit_reservation()
    copilot_checks = check_copilot_reservation()
    print(
        "PASS bounded real-code exploration: "
        f"{work_cases} work-tracking paths, {crash_checks} Eyes crash checks, "
        f"{send_checks} send-ledger checks, {edit_checks} edit-reservation check, "
        f"{copilot_checks} Copilot-reservation check"
    )
    print("Bounds: two work claims; all 6 begin/finish orders; all three Eyes baselines.")
    print("Every reachable add result and each remove result after an owned add are explored.")
    print("Teams/native bridge behavior is stubbed; no Teams account or endpoint is contacted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
