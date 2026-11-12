"""Reload the isolated Teams host while leaving the Action Server listening."""

import json
import os
import time

from _bootstrap import ROOT

from office_bridge.teams.session import SessionError, invoke


def main():
    control_path = ROOT / ".state/teams-session-control.json"
    control = json.loads(control_path.read_text(encoding="utf-8"))
    os.environ["OFFICE_TEAMS_PIPE"] = control["pipe"]
    old = invoke("teams_web_status", {}, timeout=10).get("generation")
    invoke("shutdown", {"token": control["token"]}, timeout=10)
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        try:
            result = invoke("teams_web_status", {}, timeout=3)
            if result.get("generation") and result["generation"] != old:
                print("Isolated Teams host reloaded; Action Server remained running.", flush=True)
                return
        except SessionError:
            pass
        time.sleep(1)
    raise SystemExit("Teams host did not restart; inspect .state/teams-session.stderr.log")


if __name__ == "__main__":
    main()
