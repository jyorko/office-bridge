"""Startup must verify the current server before changing client registrations."""

import sys

import complete_startup
import pytest


@pytest.mark.parametrize("dev", [False, True])
def test_completion_verifies_before_registering(monkeypatch, dev):
    steps = []

    def verify(**kwargs):
        steps.append(("verify", kwargs))
        return {"verified": True}

    def register(**kwargs):
        assert steps == [("verify", {"dev": dev})]
        steps.append(("register", kwargs))

    monkeypatch.setattr(complete_startup, "verify", verify)
    monkeypatch.setattr(complete_startup, "register", register)
    monkeypatch.setattr(sys, "argv", ["complete_startup.py"] + (["--dev"] if dev else []))
    complete_startup.main()
    assert steps == [("verify", {"dev": dev}), ("register", {"dev": dev})]


def test_failed_verification_leaves_client_registrations_untouched(monkeypatch):
    registered = []

    def verify(**kwargs):
        raise RuntimeError("Teams identity could not be verified")

    monkeypatch.setattr(complete_startup, "verify", verify)
    monkeypatch.setattr(complete_startup, "register", lambda **kwargs: registered.append(kwargs))
    monkeypatch.setattr(sys, "argv", ["complete_startup.py"])
    with pytest.raises(RuntimeError, match="Teams identity"):
        complete_startup.main()
    assert registered == []
