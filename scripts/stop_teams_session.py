"""Ask the launcher-owned Teams session to close before server shutdown."""

import ctypes
import json
import os
from ctypes import wintypes

from _bootstrap import ROOT

from office_bridge.teams.session import SessionError, invoke


def main():
    state = ROOT / ".state"
    (state / "teams-session-stopping").touch()
    try:
        control = json.loads((state / "teams-session-control.json").read_text(encoding="utf-8"))
        os.environ["OFFICE_TEAMS_PIPE"] = control["pipe"]
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x00100000, False, int(control.get("pid", 0)))
        try:
            invoke("shutdown", {"token": control["token"]}, timeout=10)
            if handle:
                kernel.WaitForSingleObject(handle, 10000)
        finally:
            if handle:
                kernel.CloseHandle(handle)
    except (OSError, ValueError, KeyError, SessionError):
        pass


if __name__ == "__main__":
    main()
