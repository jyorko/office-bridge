"""Stop only the launcher-owned Outlook session through its shutdown token."""

import ctypes
import json
import os
from ctypes import wintypes

from _bootstrap import ROOT

from office_bridge.outlook.session import SessionError, invoke


def main():
    state = ROOT / ".state"
    (state / "outlook-session-stopping").touch()
    control = json.loads((state / "outlook-session-control.json").read_text(encoding="utf-8"))
    os.environ["OFFICE_OUTLOOK_PIPE"] = control["pipe"]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x00100000, False, int(control.get("pid", 0)))
    try:
        invoke("shutdown", {"token": control["token"]}, timeout=10)
    except SessionError:
        if not handle:
            raise SystemExit("Outlook host is not available for shutdown")
    if handle:
        try:
            if kernel.WaitForSingleObject(handle, 10000) != 0:
                raise SystemExit("Outlook host did not stop within 10 seconds")
        finally:
            kernel.CloseHandle(handle)


if __name__ == "__main__":
    main()
