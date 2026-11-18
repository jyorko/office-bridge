"""Launcher-owned lifetime for the isolated Outlook WebView2 process."""

import json
import os
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path

from office_bridge.paths import NATIVE
from office_bridge.runtime.local_state import configured_account
from office_bridge.runtime.sdk import install

OUTLOOK_NATIVE = NATIVE / "outlook"


def start(root: Path) -> None:
    state = root / ".state"
    state.mkdir(exist_ok=True)
    stopping = state / "outlook-session-stopping"
    stopping.unlink(missing_ok=True)
    pipe = "office-outlook-" + secrets.token_hex(16)
    token = secrets.token_hex(32)
    os.environ["OFFICE_OUTLOOK_PIPE"] = pipe
    control = state / "outlook-session-control.json"
    control.write_text(json.dumps({"pipe": pipe, "token": token}), encoding="utf-8")
    try:
        install()
    except Exception:
        print(
            "Outlook session SDK is unavailable; Outlook actions are unavailable.", file=sys.stderr
        )
        return
    shell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"

    def supervise() -> None:
        starts = []
        while not stopping.exists():
            now = time.monotonic()
            starts[:] = [started for started in starts if now - started < 300]
            if len(starts) >= 3:
                return
            starts.append(now)
            generation = secrets.token_hex(16)
            env = os.environ.copy()
            env["OFFICE_OUTLOOK_SHUTDOWN_TOKEN"] = token
            env["OFFICE_OUTLOOK_EXPECTED_ACCOUNT"] = configured_account()
            try:
                with (state / "outlook-session.stderr.log").open("ab") as errors:
                    proc = subprocess.Popen(
                        [
                            str(shell),
                            "-NoProfile",
                            "-NonInteractive",
                            "-STA",
                            "-File",
                            str(OUTLOOK_NATIVE / "bootstrap.ps1"),
                            "-PipeName",
                            pipe,
                            "-Generation",
                            generation,
                            "-ParentPid",
                            str(os.getpid()),
                        ],
                        env=env,
                        cwd=root,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                        stdout=subprocess.DEVNULL,
                        stderr=errors,
                    )
                    pending = state / "outlook-session-control.new"
                    try:
                        pending.write_text(
                            json.dumps({"pipe": pipe, "token": token, "pid": proc.pid}),
                            encoding="utf-8",
                        )
                        pending.replace(control)
                    except OSError:
                        pass
                    proc.wait()
            except OSError:
                pass
            if not stopping.exists():
                time.sleep(2)

    threading.Thread(target=supervise, name="outlook-session-supervisor", daemon=True).start()
