"""Hand the script-free document to a separate Windows viewer over anonymous pipes."""

import hashlib
import json
import os
import queue
import re
import subprocess
import sys
import threading
from pathlib import Path

from office_bridge.paths import NATIVE
from office_bridge.presentation import Presentation
from office_bridge.runtime.local_state import config_directory
from office_bridge.runtime.sdk import install

# The runtime terminates a replaced worker's process tree. This short-lived
# launcher exits after duplicating the two pipes into the independent viewer.
LAUNCH = """import subprocess, sys
subprocess.Popen(sys.argv[1:], stdin=sys.stdin, stdout=sys.stdout, stderr=subprocess.DEVNULL,
                 creationflags=subprocess.CREATE_NO_WINDOW)
"""


def start_viewer(
    presentation: Presentation, *, verify_hidden: bool = False
) -> tuple[int, int, int]:
    if os.name != "nt" or not re.fullmatch(r"[0-9a-f]{32}", presentation.view_id):
        raise ValueError("Invalid Windows viewer request")
    shell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    args = [
        str(shell),
        "-NoProfile",
        "-NonInteractive",
        "-STA",
        "-File",
        str(NATIVE / "canvas/bootstrap.ps1"),
        "-Sdk",
        str(install()["path"]),
        "-Profile",
        str(config_directory().parent / "canvas-webview2-profile"),
        "-ViewId",
        presentation.view_id,
        "-Digest",
        hashlib.sha256(
            json.dumps(presentation.view.model_dump(), sort_keys=True).encode()
        ).hexdigest(),
        "-Count",
        str(len(presentation.view.cards)),
    ]
    if verify_hidden:
        args.append("-VerifyHidden")
    payload = presentation.html.encode("utf-8")
    launcher = subprocess.Popen(
        [sys.executable, "-c", LAUNCH, *args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    result = queue.Queue(maxsize=1)

    def exchange():
        try:
            with launcher.stdout as output:
                with launcher.stdin as stream:
                    stream.write(len(payload).to_bytes(4, "little"))
                    stream.write(payload)
                result.put(output.readline(128).decode("ascii").split())
        except (OSError, ValueError):
            result.put([])

    threading.Thread(target=exchange, daemon=True).start()
    try:
        reply = result.get(timeout=45)
    except queue.Empty:
        raise RuntimeError("Canvas viewer initialization timed out") from None
    finally:
        launcher.wait(timeout=5)
    if len(reply) != 4 or reply[0] != "READY":
        raise RuntimeError("Canvas viewer could not display this result")
    return tuple(map(int, reply[1:]))


def open_window(presentation: Presentation) -> dict:
    start_viewer(presentation)
    return {"view_id": presentation.view_id, "opened": True}
