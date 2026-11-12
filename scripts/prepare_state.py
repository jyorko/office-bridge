"""Prepare private device configuration without displaying its contents."""

import argparse
import csv
import subprocess

from _bootstrap import ROOT

from office_bridge.runtime.local_state import config_directory, prepare


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dev", action="store_true")
    args = parser.parse_args()
    config = config_directory()
    config.mkdir(parents=True, exist_ok=True)
    identity = subprocess.check_output(["whoami.exe", "/user", "/fo", "csv", "/nh"], text=True)
    sid = next(csv.reader([identity.strip()]))[1]
    result = subprocess.run(
        [
            "icacls.exe",
            str(config),
            "/inheritance:r",
            "/grant:r",
            f"*{sid}:(OI)(CI)F",
            "*S-1-5-18:(OI)(CI)F",
        ],
        capture_output=True,
    )
    if result.returncode:
        raise SystemExit("Could not restrict device configuration access")
    prepare(ROOT, dev=args.dev)
    print("Private device configuration prepared; existing credentials preserved.")


if __name__ == "__main__":
    main()
