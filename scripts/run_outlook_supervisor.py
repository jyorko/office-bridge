"""Keep the tracked Outlook session supervisor alive outside Action Server."""

import time

from _bootstrap import ROOT

from office_bridge.runtime.outlook_supervisor import start


def main():
    start(ROOT)
    stopping = ROOT / ".state/outlook-session-stopping"
    try:
        while not stopping.exists():
            time.sleep(0.5)
    except KeyboardInterrupt:
        stopping.touch()


if __name__ == "__main__":
    main()
