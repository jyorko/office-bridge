"""Bounded evidence bundles and explicit Devsy workspace delivery."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

MAX_PACK_BYTES = 1_048_576
PACK_ID = re.compile(r"^[0-9a-f]{64}$")
REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
WORKSPACE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
FILES = {"README.md", "evidence.json", "manifest.json"}


class EvidenceError(RuntimeError):
    """A bundle or its delivery could not be verified."""


class EvidenceSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reference: Annotated[str, Field(min_length=1, max_length=500)]
    captured_at: Annotated[str, Field(min_length=1, max_length=100)]
    content: Annotated[str, Field(min_length=1, max_length=50_000)]


class EvidencePackInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: Annotated[str, Field(min_length=1, max_length=200)]
    summary: Annotated[str, Field(min_length=1, max_length=100_000)]
    sources: Annotated[list[EvidenceSource], Field(min_length=1, max_length=100)]
    coverage: Annotated[str, Field(min_length=1, max_length=4000)]


class EvidencePackReceipt(BaseModel):
    pack_id: str
    path: str
    sha256: str
    size_bytes: int
    source_count: int


class EvidenceDeliveryReceipt(BaseModel):
    pack_id: str
    workspace: str
    remote_directory: str
    sha256: str
    size_bytes: int
    files: list[str]
    verified: bool


def _root() -> Path:
    root = Path(os.environ["LOCALAPPDATA"]) / "OfficeActionServer" / "evidence-packs"
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink() or root.resolve() != root.absolute():
        raise EvidenceError("Evidence storage cannot be redirected.")
    return root


def _json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_new(path: Path, data: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def create_pack(
    title: str, summary: str, sources: list[EvidenceSource], coverage: str
) -> EvidencePackReceipt:
    if not 1 <= len(title) <= 200 or not 1 <= len(summary) <= 100_000:
        raise EvidenceError("Title or summary exceeds the bundle limits.")
    if not 1 <= len(coverage) <= 4000 or not 1 <= len(sources) <= 100:
        raise EvidenceError("Coverage or source count exceeds the bundle limits.")
    sources = [EvidenceSource.model_validate(source) for source in sources]
    contents = {
        "README.md": (
            f"# {title}\n\n{summary}\n\n## Coverage and limitations\n\n{coverage}\n\n"
            "The summary is supplied by the caller. Source content and references are retained "
            "in evidence.json; their inclusion is not independent verification.\n"
        ).encode(),
        "evidence.json": _json({"schema_version": 1, "sources": [s.model_dump() for s in sources]}),
    }
    contents["manifest.json"] = _json(
        {
            "schema_version": 1,
            "files": {
                name: {"sha256": _digest(data), "size_bytes": len(data)}
                for name, data in contents.items()
            },
        }
    )
    if sum(map(len, contents.values())) > MAX_PACK_BYTES:
        raise EvidenceError("Evidence bundle exceeds the 1 MiB limit.")
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, data in sorted(contents.items()):
            archive.writestr(zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0)), data)
    data = stream.getvalue()
    if len(data) > MAX_PACK_BYTES:
        raise EvidenceError("Evidence bundle exceeds the 1 MiB limit.")
    pack_id = _digest(data)
    path = _root() / f"{pack_id}.zip"
    try:
        _write_new(path, data)
    except FileExistsError:
        if path.is_symlink() or path.read_bytes() != data:
            raise EvidenceError("Existing evidence bundle differs from its content hash.") from None
    return EvidencePackReceipt(
        pack_id=pack_id,
        path=str(path),
        sha256=pack_id,
        size_bytes=len(data),
        source_count=len(sources),
    )


def _load_pack(pack_id: str) -> bytes:
    if not PACK_ID.fullmatch(pack_id):
        raise EvidenceError("Invalid evidence pack ID.")
    path = _root() / f"{pack_id}.zip"
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_PACK_BYTES:
        raise EvidenceError("Evidence bundle is missing or exceeds its size limit.")
    data = path.read_bytes()
    if _digest(data) != pack_id:
        raise EvidenceError("Evidence bundle hash no longer matches.")
    return data


def _devsy() -> str:
    found = shutil.which("devsy")
    if found:
        return found
    installed = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "DevsyCLI" / "devsy.exe"
    if installed.is_file():
        return str(installed)
    raise EvidenceError("Devsy CLI is not installed on this device.")


def _run_devsy(arguments: list[str], payload: bytes | None = None) -> bytes:
    try:
        result = subprocess.run(
            [_devsy(), "--no-status", *arguments],
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=100,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        raise EvidenceError(
            "Devsy command did not complete; inspect the private delivery record."
        ) from None
    if result.returncode or len(result.stdout) > 1_048_576:
        raise EvidenceError("Devsy command failed; no verified delivery receipt was returned.")
    return result.stdout


def _receiver() -> str:
    source = Path(__file__).with_name("native") / "evidence_receiver.py"
    encoded = base64.b64encode(source.read_bytes()).decode("ascii")
    # Only the fixed, authored receiver enters the shell command. Bundle data uses stdin.
    return f"python3 -c \"import base64;exec(base64.b64decode('{encoded}'))\""


def send_pack(pack_id: str, workspace: str, request_id: str) -> EvidenceDeliveryReceipt:
    if not WORKSPACE.fullmatch(workspace) or not REQUEST_ID.fullmatch(request_id):
        raise EvidenceError("Invalid workspace name or delivery request ID.")
    data = _load_pack(pack_id)
    records = _root() / "deliveries"
    records.mkdir(exist_ok=True)
    path = records / f"{request_id}.json"
    claim = {"pack_id": pack_id, "workspace": workspace, "state": "reserved"}
    try:
        _write_new(path, _json(claim))
    except FileExistsError:
        existing = json.loads(path.read_bytes())
        if existing.get("pack_id") != pack_id or existing.get("workspace") != workspace:
            raise EvidenceError(
                "Delivery request ID was already used for different content."
            ) from None
        if existing.get("state") == "verified":
            return EvidenceDeliveryReceipt.model_validate(existing["receipt"])
        raise EvidenceError(
            "Delivery outcome is uncertain; reconcile the remote pack before retrying."
        ) from None
    try:
        listed = json.loads(_run_devsy(["--result-format", "json", "workspace", "list"]))
        if not isinstance(listed, list) or sum(row.get("id") == workspace for row in listed) != 1:
            raise EvidenceError("Workspace is missing or ambiguous in the Devsy inventory.")
        payload = _json(
            {
                "pack_id": pack_id,
                "workspace": workspace,
                "archive": base64.b64encode(data).decode("ascii"),
            }
        )
        output = _run_devsy(
            [
                "workspace",
                "ssh",
                workspace,
                "--agent-forwarding=false",
                "--start-services=false",
                "--command",
                _receiver(),
            ],
            payload,
        )
        value = json.loads(output)
        receipt = EvidenceDeliveryReceipt.model_validate({**value, "workspace": workspace})
        if (
            receipt.pack_id != pack_id
            or receipt.sha256 != pack_id
            or receipt.size_bytes != len(data)
            or receipt.verified is not True
            or set(receipt.files) != FILES
            or len(receipt.files) != len(FILES)
            or receipt.remote_directory
            != f"/workspaces/{workspace}/office-bridge-evidence/{pack_id}"
        ):
            raise EvidenceError("Devsy returned a mismatched delivery receipt.")
        claim.update(state="verified", receipt=receipt.model_dump())
        temporary = path.with_suffix(".verified.tmp")
        _write_new(temporary, _json(claim))
        os.replace(temporary, path)
        return receipt
    except (ValueError, OSError, KeyError, TypeError):
        raise EvidenceError(
            "Delivery did not return a valid receipt; do not retry automatically."
        ) from None
