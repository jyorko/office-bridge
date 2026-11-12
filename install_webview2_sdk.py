"""Install the pinned Microsoft WebView2 SDK files used by the Teams adapter."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import ssl
import urllib.request
import zipfile

VERSION = "1.0.4314.35"
PACKAGE_SHA256 = "90778deB1B77E36292828B4B5833C9255C27533F40E0DDD55891AE718944EDC4".lower()
BASE_URL = "https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/{v}/microsoft.web.webview2.{v}.nupkg"
FILES = {
    "lib/net462/Microsoft.Web.WebView2.Core.dll": ("Microsoft.Web.WebView2.Core.dll", "acdd517b951829f763d1255f32df1e6a936cb8623a7ebff6b8b24172dede6bd6"),
    "lib/net462/Microsoft.Web.WebView2.WinForms.dll": ("Microsoft.Web.WebView2.WinForms.dll", "c66437f80886aecf074804b61af4cc10ad90a95310c8e0a717f31ea377c26b03"),
    "runtimes/win-x64/native/WebView2Loader.dll": ("WebView2Loader.dll", "d3fce90bdeecb08596e377dea6a316354dd8949e32ff09b89d51be00c14e21b6"),
}


def _context() -> ssl.SSLContext:
    ca = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("NODE_EXTRA_CA_CERTS")
    return ssl.create_default_context(cafile=ca) if ca else ssl.create_default_context()


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def install(root: Path | None = None) -> dict[str, object]:
    if root is None:
        local = os.environ.get("LOCALAPPDATA")
        if not local:
            raise RuntimeError("LOCALAPPDATA is unavailable")
        root = Path(local) / "OfficeActionServer" / "webview2-sdk" / VERSION
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "manifest.json"
    targets = {name: root / name for name, _ in FILES.values()}

    if manifest_path.is_file() and all(p.is_file() for p in targets.values()):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            manifest = {}
        if manifest.get("version") == VERSION and manifest.get("package_sha256") == PACKAGE_SHA256:
            if all(manifest.get("files", {}).get(name) == expected == _sha(targets[name])
                   for name, expected in FILES.values()):
                return {"path": str(root), "version": VERSION, "files": len(targets)}

    package = root / f"microsoft.web.webview2.{VERSION}.nupkg"
    url = BASE_URL.format(v=VERSION)
    request = urllib.request.Request(url, headers={"User-Agent": "office-bridge-sdk-installer/1"})
    with urllib.request.urlopen(request, context=_context(), timeout=60) as response, package.open("wb") as out:
        while block := response.read(1024 * 1024):
            out.write(block)
    if _sha(package) != PACKAGE_SHA256:
        package.unlink(missing_ok=True)
        raise RuntimeError("sdk_package_integrity_failed")

    staged: dict[str, Path] = {}
    try:
        with zipfile.ZipFile(package) as archive:
            members = {item.filename: item for item in archive.infolist()}
            for member, (output_name, expected_hash) in FILES.items():
                item = members.get(member)
                if item is None or item.is_dir() or Path(member).is_absolute() or ".." in Path(member).parts:
                    raise RuntimeError("sdk_package_contents_invalid")
                target = root / (output_name + ".new")
                with archive.open(item) as source, target.open("wb") as out:
                    while block := source.read(1024 * 1024):
                        out.write(block)
                if _sha(target) != expected_hash:
                    raise RuntimeError("sdk_file_integrity_failed")
                staged[output_name] = target
        hashes = {name: _sha(path) for name, path in staged.items()}
        for name, temporary in staged.items():
            temporary.replace(targets[name])
        manifest = {"version": VERSION, "package_sha256": PACKAGE_SHA256, "files": hashes}
        manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
        return {"path": str(root), "version": VERSION, "files": len(targets)}
    finally:
        for path in staged.values():
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    result = install()
    print(f"installed WebView2 SDK {result['version']} at {result['path']} ({result['files']} files)")
