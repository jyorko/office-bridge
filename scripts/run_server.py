"""Launch the supported Actions Runtime CLI without secrets in process arguments."""

import os
import sys
import threading

from _bootstrap import ROOT
from actions.server.cli import main
from package_layout import stage_package, watch_sources

STATE = ROOT / ".state"


if __name__ == "__main__":
    os.chdir(ROOT)
    dev = "--dev" in sys.argv[1:]
    key = "" if dev else (STATE / ".api_key").read_text(encoding="utf-8").strip()
    if not dev and len(key) < 32:
        raise SystemExit("Missing local API key. Run start-office.ps1 first.")
    stage_package()
    if dev:
        threading.Thread(target=watch_sources, daemon=True).start()
    from office_bridge.runtime.supervisor import start as start_teams_session

    start_teams_session(ROOT)
    main(
        [
            "start",
            "--address",
            "127.0.0.1",
            "--port",
            "8766",
            "--dir",
            str(ROOT / ".package"),
            "--datadir",
            str(STATE / "managed-server"),
            "--min-processes",
            "1",
            "--max-processes",
            "1",
            "--full-openapi-spec",
        ]
        + (["--auto-reload"] if dev else ["--api-key", key])
    )
