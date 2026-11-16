import base64
import hashlib
import io
import json
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from office_bridge import evidence
from office_bridge.native import evidence_receiver


def setup_store(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))


def source(content="Observed output"):
    return evidence.EvidenceSource(
        reference="run://example/1",
        captured_at="2026-11-16T12:00:00Z",
        content=content,
    )


def make_pack(tmp_path, monkeypatch, *, content="Observed output"):
    setup_store(tmp_path, monkeypatch)
    return evidence.create_pack(
        "Run summary",
        "Caller supplied summary.",
        [source(content)],
        "One source; no independent validation.",
    )


def test_create_pack_is_content_addressed_and_manifest_covers_fixed_files(tmp_path, monkeypatch):
    first = make_pack(tmp_path, monkeypatch)
    second = evidence.create_pack(
        "Run summary",
        "Caller supplied summary.",
        [source()],
        "One source; no independent validation.",
    )

    assert first == second
    assert (
        first.sha256 == first.pack_id == hashlib.sha256(Path(first.path).read_bytes()).hexdigest()
    )
    with zipfile.ZipFile(first.path) as archive:
        assert set(archive.namelist()) == evidence.FILES
        readme = archive.read("README.md").decode()
        assert "Caller supplied summary." in readme
        assert "not independent verification" in readme
        evidence_json = json.loads(archive.read("evidence.json"))
        assert evidence_json["sources"][0]["content"] == "Observed output"
        manifest = json.loads(archive.read("manifest.json"))
        assert set(manifest["files"]) == evidence.FILES - {"manifest.json"}
        for name, metadata in manifest["files"].items():
            content = archive.read(name)
            assert metadata == {
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
            }
    assert first.size_bytes <= evidence.MAX_PACK_BYTES
    assert first.source_count == 1
    assert list(Path(first.path).parent.glob("*.zip")) == [Path(first.path)]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"title": "", "summary": "s", "sources": [source()], "coverage": "c"},
        {"title": "t" * 201, "summary": "s", "sources": [source()], "coverage": "c"},
        {"title": "t", "summary": "", "sources": [source()], "coverage": "c"},
        {"title": "t", "summary": "s", "sources": [], "coverage": "c"},
        {"title": "t", "summary": "s", "sources": [source()], "coverage": ""},
    ],
)
def test_create_pack_rejects_empty_or_oversized_fields_before_writing(
    tmp_path, monkeypatch, kwargs
):
    setup_store(tmp_path, monkeypatch)
    with pytest.raises((evidence.EvidenceError, ValueError)):
        evidence.create_pack(**kwargs)
    assert not (tmp_path / "OfficeActionServer" / "evidence-packs").exists()


def test_create_pack_rejects_total_bundle_over_one_mib(tmp_path, monkeypatch):
    setup_store(tmp_path, monkeypatch)
    sources = [source("x" * 50_000) for _ in range(21)]
    with pytest.raises(evidence.EvidenceError, match="1 MiB"):
        evidence.create_pack("large", "summary", sources, "coverage")
    assert not list((tmp_path / "OfficeActionServer" / "evidence-packs").glob("*.zip"))


def workspace_root(tmp_path):
    root = tmp_path / "workspaces" / "test-workspace"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _receiver_input(monkeypatch, root, payload):
    monkeypatch.setattr(evidence_receiver.Path, "cwd", classmethod(lambda cls: root))
    monkeypatch.setattr(evidence_receiver.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(payload)))


def test_receiver_verifies_and_idempotently_reuses_existing_pack(tmp_path, monkeypatch):
    pack = make_pack(tmp_path / "local", monkeypatch)
    # The production receiver runs on the Linux Devsy host; emulate its POSIX
    # permission call when exercising it under Windows pytest.
    monkeypatch.setattr(evidence_receiver.os, "fchmod", lambda *_: None, raising=False)
    archive_bytes = Path(pack.path).read_bytes()
    request = json.dumps(
        {
            "pack_id": pack.pack_id,
            "workspace": "test-workspace",
            "archive": base64.b64encode(archive_bytes).decode("ascii"),
        }
    ).encode()
    root = workspace_root(tmp_path)
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)

    _receiver_input(monkeypatch, root, request)
    receipt = evidence_receiver.receive()
    destination = root / "office-bridge-evidence" / pack.pack_id
    assert receipt["verified"] is True
    assert receipt["remote_directory"] == str(destination)
    assert {path.name for path in destination.iterdir()} == evidence.FILES | {"pack.zip"}
    assert (destination / "pack.zip").read_bytes() == archive_bytes
    exclusion = root / ".git/info/exclude"
    assert "/office-bridge-evidence/" in exclusion.read_text().splitlines()

    _receiver_input(monkeypatch, root, request)
    assert evidence_receiver.receive() == receipt
    assert exclusion.read_text().splitlines().count("/office-bridge-evidence/") == 1


def test_receiver_rejects_archive_path_traversal_without_writing(tmp_path, monkeypatch):
    setup_store(tmp_path / "local", monkeypatch)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("../outside", b"bad")
        output.writestr("README.md", b"readme")
        output.writestr("evidence.json", b"{}")
    malicious = archive.getvalue()
    request = json.dumps(
        {
            "pack_id": hashlib.sha256(malicious).hexdigest(),
            "workspace": "test-workspace",
            "archive": base64.b64encode(malicious).decode(),
        }
    ).encode()
    root = workspace_root(tmp_path)
    _receiver_input(monkeypatch, root, request)
    with pytest.raises(ValueError, match="invalid_archive"):
        evidence_receiver.receive()
    assert not (root / "office-bridge-evidence").exists()


def test_receiver_rejects_corrupt_file_manifest(tmp_path, monkeypatch):
    setup_store(tmp_path / "local", monkeypatch)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("README.md", b"readme")
        output.writestr("evidence.json", b"{}")
        output.writestr("manifest.json", json.dumps({"files": {"README.md": {"sha256": "bad"}}}))
    damaged = archive.getvalue()
    request = json.dumps(
        {
            "pack_id": hashlib.sha256(damaged).hexdigest(),
            "workspace": "test-workspace",
            "archive": base64.b64encode(damaged).decode(),
        }
    ).encode()
    _receiver_input(monkeypatch, workspace_root(tmp_path), request)
    with pytest.raises(ValueError, match="invalid_manifest"):
        evidence_receiver.receive()


def test_send_pack_records_verified_delivery_and_replays_without_second_send(tmp_path, monkeypatch):
    pack = make_pack(tmp_path, monkeypatch)
    calls = []
    remote_path = f"/workspaces/test-workspace/office-bridge-evidence/{pack.pack_id}"

    def run_devsy(arguments, payload=None):
        calls.append((arguments, payload))
        if "list" in arguments:
            return json.dumps([{"id": "test-workspace"}]).encode()
        request = json.loads(payload)
        archive = base64.b64decode(request["archive"], validate=True)
        assert request["pack_id"] == pack.pack_id
        assert request["workspace"] == "test-workspace"
        return json.dumps(
            {
                "pack_id": pack.pack_id,
                "remote_directory": remote_path,
                "sha256": hashlib.sha256(archive).hexdigest(),
                "size_bytes": len(archive),
                "files": sorted(evidence.FILES),
                "verified": True,
            }
        ).encode()

    monkeypatch.setattr(evidence, "_run_devsy", run_devsy)
    receipt = evidence.send_pack(pack.pack_id, "test-workspace", "delivery-1")
    replay = evidence.send_pack(pack.pack_id, "test-workspace", "delivery-1")
    assert receipt == replay
    assert receipt.verified is True
    assert len(calls) == 2
    assert calls[0][1] is None
    assert calls[1][1] is not None
    record = json.loads(
        (tmp_path / "OfficeActionServer/evidence-packs/deliveries/delivery-1.json").read_text()
    )
    assert record["state"] == "verified"
    assert "receipt" in record


def test_send_pack_conflicting_request_id_is_rejected_before_second_delivery(tmp_path, monkeypatch):
    pack = make_pack(tmp_path, monkeypatch)
    other = evidence.create_pack("Other", "Other summary", [source("other")], "Other coverage")
    calls = []

    def run_devsy(arguments, payload=None):
        calls.append(arguments)
        if "list" in arguments:
            return json.dumps([{"id": "test-workspace"}]).encode()
        request = json.loads(payload)
        archive = base64.b64decode(request["archive"], validate=True)
        pack_id = request["pack_id"]
        return json.dumps(
            {
                "pack_id": pack_id,
                "remote_directory": f"/workspaces/test-workspace/office-bridge-evidence/{pack_id}",
                "sha256": hashlib.sha256(archive).hexdigest(),
                "size_bytes": len(archive),
                "files": sorted(evidence.FILES),
                "verified": True,
            }
        ).encode()

    monkeypatch.setattr(evidence, "_run_devsy", run_devsy)
    evidence.send_pack(pack.pack_id, "test-workspace", "same-request")
    with pytest.raises(evidence.EvidenceError, match="different content"):
        evidence.send_pack(other.pack_id, "test-workspace", "same-request")
    assert len(calls) == 2


def test_uncertain_devsy_delivery_is_durably_blocked_from_automatic_retry(tmp_path, monkeypatch):
    pack = make_pack(tmp_path, monkeypatch)
    calls = []

    def run_devsy(arguments, payload=None):
        calls.append((arguments, payload))
        if "list" in arguments:
            return json.dumps([{"id": "test-workspace"}]).encode()
        raise evidence.EvidenceError(
            "Devsy command did not complete; inspect the private delivery record."
        )

    monkeypatch.setattr(evidence, "_run_devsy", run_devsy)
    with pytest.raises(evidence.EvidenceError, match="did not complete"):
        evidence.send_pack(pack.pack_id, "test-workspace", "uncertain-1")
    with pytest.raises(evidence.EvidenceError, match="uncertain"):
        evidence.send_pack(pack.pack_id, "test-workspace", "uncertain-1")
    assert len(calls) == 2
    record = json.loads(
        (tmp_path / "OfficeActionServer/evidence-packs/deliveries/uncertain-1.json").read_text()
    )
    assert record == {"pack_id": pack.pack_id, "workspace": "test-workspace", "state": "reserved"}


@pytest.mark.parametrize(
    "pack_id,workspace,request_id",
    [
        ("bad", "test-workspace", "request-1"),
        ("a" * 64, "../test-workspace", "request-1"),
        ("a" * 64, "test-workspace", "../request-1"),
    ],
)
def test_send_pack_rejects_invalid_identifiers_without_devsy_calls(
    tmp_path, monkeypatch, pack_id, workspace, request_id
):
    setup_store(tmp_path, monkeypatch)
    monkeypatch.setattr(
        evidence, "_run_devsy", lambda *args, **kwargs: pytest.fail("no Devsy call")
    )
    with pytest.raises(evidence.EvidenceError):
        evidence.send_pack(pack_id, workspace, request_id)
