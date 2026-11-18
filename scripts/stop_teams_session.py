"""Ask launcher-owned Teams and Outlook sessions to close before server shutdown."""

import ctypes
import json
import os
from ctypes import wintypes

from _bootstrap import ROOT

from office_bridge.outlook.session import SessionError as OutlookSessionError
from office_bridge.outlook.session import invoke as invoke_outlook
from office_bridge.teams.session import SessionError as TeamsSessionError
from office_bridge.teams.session import invoke as invoke_teams


def stop_session(state, name, environment, invoke):
    (state / f"{name}-session-stopping").touch()
    try:
        control = json.loads((state / f"{name}-session-control.json").read_text(encoding="utf-8"))
        os.environ[environment] = control["pipe"]
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x00100000, False, int(control.get("pid", 0)))
        if not handle:
            return
        try:
            invoke("shutdown", {"token": control["token"]}, timeout=10)
            kernel.WaitForSingleObject(handle, 10000)
        finally:
            kernel.CloseHandle(handle)
    except (OSError, ValueError, KeyError, TeamsSessionError, OutlookSessionError):
        pass


def main():
    state = ROOT / ".state"
    stop_session(state, "teams", "OFFICE_TEAMS_PIPE", invoke_teams)
    stop_session(state, "outlook", "OFFICE_OUTLOOK_PIPE", invoke_outlook)


if __name__ == "__main__":
    main()
