"""Fixed remote receiver; no shell arguments or archive paths are executed."""

import base64
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

MAX_BYTES = 1_048_576
FILES = {"README.md", "evidence.json", "manifest.json"}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def receive():
    raw = sys.stdin.buffer.read(1_500_001)
    if len(raw) > 1_500_000:
        raise ValueError("payload_too_large")
    request = json.loads(raw)
    pack_id = request["pack_id"]
    if not re.fullmatch(r"[0-9a-f]{64}", pack_id):
        raise ValueError("invalid_pack_id")
    data = base64.b64decode(request["archive"], validate=True)
    if len(data) > MAX_BYTES or digest(data) != pack_id:
        raise ValueError("archive_hash_mismatch")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        if (
            len(entries) != 3
            or {e.filename for e in entries} != FILES
            or sum(e.file_size for e in entries) > MAX_BYTES
        ):
            raise ValueError("invalid_archive")
        contents = {entry.filename: archive.read(entry) for entry in entries}
    manifest = json.loads(contents["manifest.json"])
    if set(manifest["files"]) != FILES - {"manifest.json"}:
        raise ValueError("invalid_manifest")
    for name, metadata in manifest["files"].items():
        if metadata != {"sha256": digest(contents[name]), "size_bytes": len(contents[name])}:
            raise ValueError("file_hash_mismatch")
    workspace = request["workspace"]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}", workspace):
        raise ValueError("invalid_workspace")
    workspace_root = Path.cwd()
    if workspace_root.name != workspace or workspace_root.parent.name != "workspaces":
        raise ValueError("unexpected_workspace_directory")
    root = workspace_root / "office-bridge-evidence"
    if root.is_symlink() or root.resolve() != root.absolute():
        raise ValueError("redirected_destination")
    root.mkdir(exist_ok=True, mode=0o700)
    git = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=workspace_root,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if git.returncode == 0:
        repository = Path(git.stdout.strip()).resolve()
        relative = root.relative_to(repository).as_posix()
        exclusion = "/" + relative + "/"
        location = subprocess.run(
            ["git", "rev-parse", "--git-path", "info/exclude"],
            cwd=workspace_root,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        exclude = Path(location.stdout.strip())
        if not exclude.is_absolute():
            exclude = workspace_root / exclude
        if exclude.is_symlink():
            raise ValueError("redirected_git_exclude")
        existing = exclude.read_text() if exclude.exists() else ""
        if exclusion not in existing.splitlines():
            exclude.parent.mkdir(parents=True, exist_ok=True)
            with exclude.open("a") as stream:
                stream.write(
                    ("\n" if existing and not existing.endswith("\n") else "") + exclusion + "\n"
                )
                stream.flush()
                os.fsync(stream.fileno())
    destination = root / pack_id

    def verify():
        if destination.is_symlink() or not destination.is_dir():
            raise ValueError("destination_conflict")
        expected = {**contents, "pack.zip": data}
        if {p.name for p in destination.iterdir()} != set(expected):
            raise ValueError("destination_conflict")
        for name, value in expected.items():
            path = destination / name
            if path.is_symlink() or path.read_bytes() != value:
                raise ValueError("destination_conflict")

    if destination.exists() or destination.is_symlink():
        verify()
    else:
        stage = Path(tempfile.mkdtemp(prefix=".incoming-", dir=root))
        try:
            for name, value in {**contents, "pack.zip": data}.items():
                with (stage / name).open("xb") as stream:
                    os.fchmod(stream.fileno(), 0o600)
                    stream.write(value)
                    stream.flush()
                    os.fsync(stream.fileno())
            try:
                os.rename(stage, destination)
            except OSError:
                if not destination.exists():
                    raise
            verify()
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    return {
        "pack_id": pack_id,
        "remote_directory": str(destination),
        "sha256": digest((destination / "pack.zip").read_bytes()),
        "size_bytes": len(data),
        "files": sorted(FILES),
        "verified": True,
    }


if __name__ == "__main__":
    try:
        print(json.dumps(receive()))
    except Exception:
        print("Evidence receiver rejected the delivery.", file=sys.stderr)
        raise SystemExit(1) from None
