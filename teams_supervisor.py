"""Launcher-owned lifetime for the isolated Teams WebView2 process."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time


def start(root: Path):
    from install_webview2_sdk import install

    state = root / ".state"
    state.mkdir(exist_ok=True)
    stopping = state / "teams-session-stopping"
    stopping.unlink(missing_ok=True)
    pipe = "office-teams-" + secrets.token_hex(16)
    token = secrets.token_hex(32)
    os.environ["OFFICE_TEAMS_PIPE"] = pipe
    control = state / "teams-session-control.json"
    control.write_text(json.dumps({"pipe": pipe, "token": token}), encoding="utf-8")
    try:
        install()
    except Exception:
        print("Teams session SDK is unavailable; Office actions remain available.", file=sys.stderr)
        return
    shell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"

    def supervise():
        starts = []
        while not stopping.exists():
            now = time.monotonic()
            starts[:] = [t for t in starts if now - t < 300]
            if len(starts) >= 3:
                return
            starts.append(now)
            generation = secrets.token_hex(16)
            env = os.environ.copy()
            env["OFFICE_TEAMS_SHUTDOWN_TOKEN"] = token
            env["OFFICE_TEAMS_TARGETS_FILE"] = str(state / "teams-targets.json")
            env["OFFICE_TEAMS_SCRIPT_FILE"] = str(root / "powershell/teams_group.js")
            env["OFFICE_TEAMS_HOST_SOURCE"] = str(root / "powershell/TeamsSession.cs")
            try:
                with (state / "teams-session.stderr.log").open("ab") as errors:
                    proc = subprocess.Popen([str(shell), "-NoProfile", "-NonInteractive", "-STA", "-File",
                        str(root / "powershell/teams_session.ps1"), "-PipeName", pipe, "-Generation", generation,
                        "-ParentPid", str(os.getpid())], env=env, cwd=root,
                        creationflags=subprocess.CREATE_NO_WINDOW, stdout=subprocess.DEVNULL, stderr=errors)
                    pending = state / "teams-session-control.new"
                    try:
                        pending.write_text(json.dumps({"pipe": pipe, "token": token, "pid": proc.pid}), encoding="utf-8")
                        pending.replace(control)
                    except OSError:
                        pass
                    proc.wait()
            except OSError:
                pass
            if not stopping.exists():
                time.sleep(2)

    threading.Thread(target=supervise, name="teams-session-supervisor", daemon=True).start()
