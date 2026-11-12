"""Install the SHA-256-pinned Windows Actions Runtime release executable."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import ssl
import subprocess
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "1.1.0"
ASSET = f"actions-runtime-{VERSION}-windows64.exe"
RELEASE = f"https://github.com/joshyorko/actions/releases/download/actions-runtime-{VERSION}"
ASSET_URL = f"{RELEASE}/{ASSET}"
CHECKSUM_URL = f"{RELEASE}/actions-runtime-{VERSION}-sha256.txt"
CHECKSUM_SHA256 = "54442a195b692a3a2fd5efe3789d5a61390d5022fb5b9aaa29db720a43324b2c"
ASSET_SHA256 = "44507e8dcd82fb367c616c699957611096bc05ff2ae5e0f41a11b98a98332098"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "office-bridge-bootstrap/1"})
    with urllib.request.urlopen(
        request, context=ssl.create_default_context(), timeout=120
    ) as response:
        with destination.open("wb") as output:
            shutil.copyfileobj(response, output, length=1024 * 1024)


def secure(path: Path, *, directory: bool) -> None:
    identity = subprocess.check_output(["whoami.exe", "/user", "/fo", "csv", "/nh"], text=True)
    # The SID is the second quoted CSV field; it avoids localized account names in ACL syntax.
    import csv

    sid = next(csv.reader([identity.strip()]))[1]
    args = ["icacls.exe", str(path), "/inheritance:r", "/grant:r", f"*{sid}:F", "*S-1-5-18:F"]
    if directory:
        args[-2:] = [f"*{sid}:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F"]
    result = subprocess.run(args, capture_output=True)
    if result.returncode:
        raise RuntimeError(f"Could not restrict access to {path}")


def verify_checksum_manifest(path: Path) -> None:
    if sha256(path) != CHECKSUM_SHA256:
        raise RuntimeError("The official release checksum file failed its pinned SHA-256 check")
    text = path.read_text(encoding="utf-8")
    match = re.search(rf"(?m)^([0-9a-f]{{64}})\s+{re.escape(ASSET)}\s*$", text)
    if not match or match.group(1) != ASSET_SHA256:
        raise RuntimeError(
            "The official checksum manifest does not match the pinned executable hash"
        )


def install() -> Path:
    if os.name != "nt":
        raise SystemExit("The Actions Runtime executable installer requires Windows.")
    target = Path.home() / ".local" / "bin" / "action-server.exe"
    target.parent.mkdir(parents=True, exist_ok=True)
    state = ROOT / ".state"
    state.mkdir(parents=True, exist_ok=True)
    secure(state, directory=True)

    if target.is_file() and sha256(target) == ASSET_SHA256:
        print(f"Actions Runtime {VERSION} is already installed at {target}.")
        return target

    with tempfile.TemporaryDirectory(prefix="actions-runtime-", dir=target.parent) as temp_dir:
        staging = Path(temp_dir)
        checksum_file = staging / "release-sha256.txt"
        executable = staging / ASSET
        download(CHECKSUM_URL, checksum_file)
        verify_checksum_manifest(checksum_file)
        download(ASSET_URL, executable)
        if sha256(executable) != ASSET_SHA256:
            raise RuntimeError("The downloaded Actions Runtime executable failed its SHA-256 check")

        backup = state / "action-server.exe.before-runtime-1.1.0"
        if target.exists() and not backup.exists():
            shutil.copy2(target, backup)
            secure(backup, directory=False)
        os.replace(executable, target)

    if sha256(target) != ASSET_SHA256:
        raise RuntimeError("Installed Actions Runtime executable failed its SHA-256 check")
    print(f"Installed Actions Runtime {VERSION} at {target} (SHA-256 verified).")
    return target


if __name__ == "__main__":
    install()
