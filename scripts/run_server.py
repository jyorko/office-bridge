"""Launch the supported Actions Runtime CLI without secrets in process arguments."""

import os
import secrets
import sys
import threading
from pathlib import Path

from _bootstrap import ROOT
from actions.server.cli import main
from package_layout import stage_package, watch_sources
from prepare_state import prepare_local_state

from office_bridge.runtime.endpoint import DEFAULT_PORT, startup_port
from office_bridge.runtime.local_state import configured_account

STATE = ROOT / ".state"


if __name__ == "__main__":
    os.chdir(ROOT)
    os.environ.setdefault(
        "ACTIONS_HOME", str(Path(os.environ["LOCALAPPDATA"]) / "OfficeActionServer/rcc-actions")
    )
    dev = "--dev" in sys.argv[1:]
    prepare_local_state(dev=dev, teams_account=os.environ.get("OFFICE_BRIDGE_TEAMS_ACCOUNT"))
    os.environ.pop("OFFICE_BRIDGE_TEAMS_ACCOUNT", None)
    os.environ["OFFICE_BRIDGE_ACCOUNT"] = configured_account()
    os.environ["OFFICE_CANVAS_SIGNING_KEY"] = secrets.token_hex(32)
    os.environ["OFFICE_CANVAS_ROOT"] = str(ROOT)
    stage_package()
    if dev:
        threading.Thread(target=watch_sources, daemon=True).start()
    from office_bridge.runtime.outlook_supervisor import start as start_outlook_session
    from office_bridge.runtime.supervisor import start as start_teams_session

    start_teams_session(ROOT)
    start_outlook_session(ROOT)
    raise SystemExit(
        main(
            [
                "start",
                "--address",
                "127.0.0.1",
                "--port",
                str(startup_port(int(os.environ.get("OFFICE_BRIDGE_PORT", DEFAULT_PORT)))),
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
            + (["--auto-reload"] if dev else [])
        )
    )
