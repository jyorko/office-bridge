"""Bounded client for the launcher-owned, local Teams session."""

import ctypes
import json
import os
import struct
import time
from ctypes import wintypes

MAX_FRAME = 1_048_576


class SessionError(RuntimeError):
    pass


def invoke(operation: str, arguments: dict, timeout: float = 60) -> dict:
    if operation not in {
        "teams_web_status",
        "teams_web_send_group",
        "teams_web_send_self",
        "teams_web_read_group",
        "teams_web_read_self",
        "shutdown",
    }:
        raise SessionError("invalid_operation")
    name = os.environ.get("OFFICE_TEAMS_PIPE", "")
    if not name or os.name != "nt":
        raise SessionError("session_unavailable")
    payload = json.dumps(
        {"version": 1, "operation": operation, "arguments": arguments}, ensure_ascii=False
    ).encode("utf-8")
    if len(payload) > MAX_FRAME:
        raise SessionError("invalid_request")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel.WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
    kernel.ReadFile.argtypes = [
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    kernel.WriteFile.argtypes = kernel.ReadFile.argtypes
    kernel.PeekNamedPipe.argtypes = [
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    path = "\\\\.\\pipe\\" + name
    deadline = time.monotonic() + timeout
    handle = None
    try:
        while time.monotonic() < deadline:
            handle = kernel.CreateFileW(path, 0xC0000000, 0, None, 3, 0, None)
            if handle != ctypes.c_void_p(-1).value:
                break
            handle = None
            kernel.WaitNamedPipeW(path, 250)
            time.sleep(0.05)
        if handle is None:
            raise SessionError("session_unavailable")
        wire = struct.pack("<I", len(payload)) + payload
        sent = wintypes.DWORD()
        if not kernel.WriteFile(
            handle, wire, len(wire), ctypes.byref(sent), None
        ) or sent.value != len(wire):
            raise SessionError("session_write_failed")

        def read_exact(length):
            result = bytearray()
            while len(result) < length:
                if time.monotonic() >= deadline:
                    raise SessionError("session_timeout")
                available = wintypes.DWORD()
                if not kernel.PeekNamedPipe(handle, None, 0, None, ctypes.byref(available), None):
                    raise SessionError("session_disconnected")
                if not available.value:
                    time.sleep(0.05)
                    continue
                n = min(length - len(result), available.value)
                buffer = ctypes.create_string_buffer(n)
                received = wintypes.DWORD()
                if (
                    not kernel.ReadFile(handle, buffer, n, ctypes.byref(received), None)
                    or not received.value
                ):
                    raise SessionError("session_disconnected")
                result.extend(buffer.raw[: received.value])
            return bytes(result)

        size = struct.unpack("<I", read_exact(4))[0]
        if size < 2 or size > MAX_FRAME:
            raise SessionError("invalid_response")
        envelope = json.loads(read_exact(size))
        if (
            envelope.get("version") == 1
            and envelope.get("ok") is False
            and envelope.get("error")
            in {"draft_exists", "identity_failed", "failed", "host_operation_failed"}
        ):
            raise SessionError(envelope["error"])
        if (
            envelope.get("version") != 1
            or envelope.get("ok") is not True
            or not isinstance(envelope.get("result"), dict)
        ):
            raise SessionError("host_operation_failed")
        return envelope["result"]
    finally:
        if handle is not None:
            kernel.CloseHandle(handle)
