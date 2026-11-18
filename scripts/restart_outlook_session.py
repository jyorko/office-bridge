"""Reload the isolated Outlook host while leaving the Action Server running."""

import hashlib
import json
import os
import time

from _bootstrap import ROOT

from office_bridge.outlook.session import SessionError, invoke


def main():
    control_path = ROOT / ".state/outlook-session-control.json"
    control = json.loads(control_path.read_text(encoding="utf-8"))
    os.environ["OFFICE_OUTLOOK_PIPE"] = control["pipe"]
    old = invoke("outlook_status", {}, timeout=30).get("generation")
    invoke("shutdown", {"token": control["token"]}, timeout=10)
    deadline = time.monotonic() + 45
    expected_hash = hashlib.sha256(
        (ROOT / "src/office_bridge/native/outlook/OutlookSession.cs").read_bytes()
    ).hexdigest()
    while time.monotonic() < deadline:
        try:
            result = invoke("outlook_status", {}, timeout=5)
            if (
                result.get("generation")
                and result["generation"] != old
                and result.get("source_sha256") == expected_hash
            ):
                print("Isolated Outlook host reloaded.", flush=True)
                return
        except SessionError:
            pass
        time.sleep(1)
    raise SystemExit("Outlook host did not restart; inspect .state/outlook-session.stderr.log")


if __name__ == "__main__":
    main()
