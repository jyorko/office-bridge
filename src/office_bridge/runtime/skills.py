"""Install repository skills for the current user without overwriting user edits."""

import hashlib
import json
import os
from pathlib import Path

NAMES = ("office-bridge-mcp", "verify-office-bridge-safety")
MANIFEST = ".office-bridge-managed.json"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def install(repo: Path, destination: Path) -> list[Path]:
    repo = repo.resolve()
    destination = destination.resolve()
    prepared = []
    for name in NAMES:
        source = repo / ".agents" / "skills" / name
        target = destination / name
        if target.is_symlink():
            raise ValueError(f"Refusing linked skill directory: {target}")
        files = {}
        for path in source.rglob("*"):
            if path.is_symlink():
                raise ValueError(f"Refusing linked source: {path}")
            if path.is_file():
                files[path.relative_to(source).as_posix()] = path.read_bytes()
        if "SKILL.md" not in files:
            raise ValueError(f"Missing skill source: {source}")
        files["references/installation.md"] = (
            "# Installed Office Bridge checkout\n\n"
            f"Repository: `{repo}`\n\n"
            "Use this checkout for lifecycle and verification commands, regardless "
            "of the current working directory. Read its `.state/server.json` for the "
            "live endpoint. Do not start another server. If the checkout has moved, "
            "rerun its skill installer.\n"
        ).encode()
        old = {}
        if target.exists():
            manifest = target / MANIFEST
            if not manifest.is_file():
                raise ValueError(f"Existing skill is not managed by Office Bridge: {target}")
            record = json.loads(manifest.read_text(encoding="utf-8"))
            if record.get("owner") != "office-bridge":
                raise ValueError(f"Unrecognized skill owner: {target}")
            old = record["files"]
        for relative in set(old) | set(files):
            path = target / relative
            if not path.resolve().is_relative_to(target.resolve()) or any(
                parent.is_symlink() for parent in (path, *path.parents) if parent != destination
            ):
                raise ValueError(f"Unsafe installed skill path: {path}")
            if path.exists() and (
                relative not in old or digest(path.read_bytes()) != old[relative]
            ):
                raise ValueError(f"Preserving locally modified skill file: {path}")
        prepared.append((target, files, old))

    for target, files, old in prepared:
        target.mkdir(parents=True, exist_ok=True)
        for relative, data in files.items():
            path = target / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + ".office-bridge.tmp")
            temporary.write_bytes(data)
            os.replace(temporary, path)
        for relative in set(old) - set(files):
            (target / relative).unlink(missing_ok=True)
        record = {"owner": "office-bridge", "files": {k: digest(v) for k, v in files.items()}}
        temporary = target / (MANIFEST + ".tmp")
        temporary.write_text(json.dumps(record, indent=2), encoding="utf-8")
        os.replace(temporary, target / MANIFEST)
    return [target for target, _, _ in prepared]
