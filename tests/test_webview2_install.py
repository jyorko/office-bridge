import hashlib
import io
import zipfile

import pytest

from office_bridge.runtime import sdk


def package_fixture(monkeypatch):
    payloads = {
        member: (name, b"test SDK payload: " + name.encode())
        for member, (name, _) in sdk.FILES.items()
    }
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        for member, (_, content) in payloads.items():
            bundle.writestr(member, content)
        bundle.writestr("../outside.txt", b"must not be extracted")
    data = archive.getvalue()
    monkeypatch.setattr(
        sdk,
        "FILES",
        {
            member: (name, hashlib.sha256(content).hexdigest())
            for member, (name, content) in payloads.items()
        },
    )
    monkeypatch.setattr(sdk, "PACKAGE_SHA256", hashlib.sha256(data).hexdigest())
    monkeypatch.setattr(sdk, "_context", lambda: None)
    monkeypatch.setattr(sdk.urllib.request, "urlopen", lambda *args, **kwargs: io.BytesIO(data))
    return payloads


def test_install_extracts_only_pinned_files_and_reuses_verified_cache(tmp_path, monkeypatch):
    payloads = package_fixture(monkeypatch)
    destination = tmp_path / "sdk"
    result = sdk.install(destination)
    assert result["files"] == 3
    for name, content in payloads.values():
        assert (destination / name).read_bytes() == content
    assert not (tmp_path / "outside.txt").exists()

    def offline(*args, **kwargs):
        raise AssertionError("verified cache must not need the network")

    monkeypatch.setattr(sdk.urllib.request, "urlopen", offline)
    assert sdk.install(destination) == result


def test_package_hash_failure_never_installs_dlls(tmp_path, monkeypatch):
    package_fixture(monkeypatch)
    monkeypatch.setattr(sdk, "PACKAGE_SHA256", "0" * 64)
    with pytest.raises(RuntimeError, match="sdk_package_integrity_failed"):
        sdk.install(tmp_path / "sdk")
    assert not list(tmp_path.rglob("*.dll"))
    assert not (tmp_path / "outside.txt").exists()


def test_corrupted_cached_dll_is_repaired_from_verified_package(tmp_path, monkeypatch):
    payloads = package_fixture(monkeypatch)
    destination = tmp_path / "sdk"
    sdk.install(destination)
    name, content = next(iter(payloads.values()))
    (destination / name).write_bytes(b"corrupted")
    sdk.install(destination)
    assert (destination / name).read_bytes() == content
