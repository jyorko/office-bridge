"""Verify the running server before registering its endpoint with desktop clients."""

import argparse
import json

from bootstrap_check import verify
from register_mcp import register


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dev", action="store_true")
    args = parser.parse_args()
    print("Waiting for the managed Action Server and verifying Teams (read-only)...", flush=True)
    report = verify(dev=args.dev)
    print(json.dumps(report, indent=2))
    register(dev=args.dev)


if __name__ == "__main__":
    main()
