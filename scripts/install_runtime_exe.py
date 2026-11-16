"""Install the SHA-256-pinned official Actions Runtime executable."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import ssl
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE = json.loads((ROOT / "scripts" / "runtime-release.json").read_text(encoding="utf-8"))


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def download(url: str, path: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "office-bridge-bootstrap/1"})
    with urllib.request.urlopen(
        request, context=ssl.create_default_context(), timeout=120
    ) as response:
        with path.open("wb") as output:
            shutil.copyfileobj(response, output, length=1024 * 1024)


def main() -> None:
    if os.name != "nt":
        raise SystemExit("This installer requires Windows.")
    target = Path.home() / ".local" / "bin" / "action-server.exe"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and digest(target) == RELEASE["asset_sha256"]:
        print(f"Actions Runtime {RELEASE['version']} is already installed at {target}.")
        return
    with tempfile.TemporaryDirectory(prefix="actions-runtime-", dir=target.parent) as temp:
        stage = Path(temp)
        asset = RELEASE["asset"]
        manifest = stage / "checksums.txt"
        binary = stage / asset
        base = RELEASE["release_url"]
        download(f"{base}/actions-runtime-{RELEASE['version']}-sha256.txt", manifest)
        if digest(manifest) != RELEASE["checksum_sha256"]:
            raise RuntimeError("Official checksum manifest hash mismatch")
        expected = f"{RELEASE['asset_sha256']}  {asset}"
        if expected not in manifest.read_text(encoding="utf-8"):
            raise RuntimeError(
                "Official checksum manifest does not match the pinned executable hash"
            )
        download(f"{base}/{asset}", binary)
        if digest(binary) != RELEASE["asset_sha256"]:
            raise RuntimeError("Downloaded Actions Runtime executable hash mismatch")
        if target.exists():
            backup = ROOT / ".state" / f"action-server.exe.before-runtime-{RELEASE['version']}"
            backup.parent.mkdir(parents=True, exist_ok=True)
            if not backup.exists():
                shutil.copy2(target, backup)
        os.replace(binary, target)
    print(f"Installed Actions Runtime {RELEASE['version']} at {target}.")


if __name__ == "__main__":
    main()
