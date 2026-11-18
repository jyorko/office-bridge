"""Show or hide the isolated Teams inspection window without activating it."""

import argparse
import json
import os

from _bootstrap import ROOT

from office_bridge.teams.session import invoke


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--visible", action=argparse.BooleanOptionalAction, required=True)
    args = parser.parse_args()
    control = json.loads((ROOT / ".state/teams-session-control.json").read_text(encoding="utf-8"))
    os.environ["OFFICE_TEAMS_PIPE"] = control["pipe"]
    result = invoke("set_visibility", {"token": control["token"], "visible": args.visible})
    assert result["visible"] is args.visible
    print(
        "Isolated Teams window is visible." if args.visible else "Isolated Teams window is hidden."
    )


if __name__ == "__main__":
    main()
