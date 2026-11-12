"""Prepare the pinned Action Server RCC and merge Windows trust into its active profile."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".state"


def secure(path: Path, directory: bool = False) -> None:
    identity = subprocess.check_output(["whoami.exe"], text=True).strip()
    access = ":(OI)(CI)F" if directory else ":F"
    flags = ["/inheritance:r", "/grant:r", identity + access, "*S-1-5-18" + access]
    result = subprocess.run(["icacls.exe", str(path), *flags], capture_output=True)
    if result.returncode:
        raise RuntimeError(f"Could not restrict access to {path}")


def run(args: list[str], *, env: dict[str, str], capture: bool = False) -> str:
    result = subprocess.run(args, env=env, text=True, capture_output=capture)
    if result.returncode:
        detail = result.stderr.strip() if capture else ""
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(args)} {detail}")
    return result.stdout if capture else ""


def windows_roots() -> list[str]:
    script = (
        "$certs = Get-ChildItem Cert:\\CurrentUser\\Root,Cert:\\LocalMachine\\Root | "
        "Sort-Object Thumbprint -Unique; "
        "@($certs | ForEach-Object { [Convert]::ToBase64String($_.RawData) }) | "
        "ConvertTo-Json -Compress"
    )
    output = subprocess.check_output(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], text=True
    )
    roots = json.loads(output)
    if len(roots) < 10:
        raise RuntimeError("Could not read the Windows trusted root certificate stores")
    return [
        "-----BEGIN CERTIFICATE-----\n"
        + "\n".join(cert[index : index + 64] for index in range(0, len(cert), 64))
        + "\n-----END CERTIFICATE-----"
        for cert in roots
    ]


def cert_ids(bundle: str) -> set[str]:
    found = set()
    for body in re.findall(
        r"-----BEGIN CERTIFICATE-----(.*?)-----END CERTIFICATE-----", bundle, re.DOTALL
    ):
        try:
            found.add(hashlib.sha256(base64.b64decode("".join(body.split()))).hexdigest())
        except ValueError:
            continue
    return found


def prepare_profile(rcc: Path, home: Path, label: str, roots: list[str]) -> str:
    home.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["ROBOCORP_HOME"] = str(home)
    diagnostics = json.loads(
        run([str(rcc), "configuration", "diagnostics", "--quick", "--json"], env=env, capture=True)
    )
    active_name = diagnostics.get("details", {}).get("config-active-profile") or "default"
    original = STATE / f"{label}-rcc-active-profile.before.yaml"
    exported = STATE / f"{label}-rcc-active-profile.export.yaml"
    profile = STATE / f"{label}-rcc-active-profile.yaml"
    try:
        run(
            [
                str(rcc),
                "configuration",
                "export",
                "--profile",
                active_name,
                "--filename",
                str(exported),
            ],
            env=env,
            capture=True,
        )
        secure(exported)
        if not original.exists():
            shutil.copyfile(exported, original)
            secure(original)
        data = yaml.safe_load(exported.read_text(encoding="utf-8")) or {}
    except RuntimeError:
        if active_name != "default" or exported.exists():
            raise
        data = {}
        active_name = "office-bridge-windows-ca"

    settings = data.setdefault("settings", {})
    meta = settings.setdefault("meta", {})
    meta["name"] = active_name
    meta.setdefault("source", "Office Bridge bootstrap using Windows certificate stores")
    meta.setdefault("version", "1")
    certificates = settings.setdefault("certificates", {})
    certificates["verify-ssl"] = True
    certificates["ssl-no-revoke"] = True
    certificates.setdefault("legacy-renegotiation-allowed", False)

    existing = data.get("ca-bundle", "") or ""
    known = cert_ids(existing)
    for cert in roots:
        identity = next(iter(cert_ids(cert)), None)
        if identity and identity not in known:
            existing = (existing.rstrip() + "\n" + cert).lstrip()
            known.add(identity)
    data["ca-bundle"] = existing + "\n"
    data["name"] = active_name
    data.setdefault("description", "Windows trusted roots; SSL verification enabled.")
    profile.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    secure(profile)
    run([str(rcc), "configuration", "import", "--filename", str(profile), "--switch"], env=env)
    run([str(rcc), "configuration", "switch", "--profile", active_name], env=env)
    merged_name = (
        json.loads(
            run(
                [str(rcc), "configuration", "diagnostics", "--quick", "--json"],
                env=env,
                capture=True,
            )
        )
        .get("details", {})
        .get("config-active-profile")
    )
    if merged_name != active_name:
        raise RuntimeError(f"RCC active profile changed unexpectedly: {merged_name!r}")
    return active_name


def main() -> None:
    if os.name != "nt":
        raise SystemExit("This bootstrap helper requires Windows.")
    STATE.mkdir(parents=True, exist_ok=True)
    secure(STATE, directory=True)
    runtime_python = ROOT / ".runtime" / "Scripts" / "python.exe"
    if not runtime_python.is_file():
        raise RuntimeError("Pinned runtime is missing; run setup.ps1 first")

    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise RuntimeError("LOCALAPPDATA is unavailable")
    actions_home = Path(
        os.environ.get("ACTIONS_HOME")
        or Path(local_app_data) / "OfficeActionServer" / "rcc-actions"
    )
    os.environ["ACTIONS_HOME"] = str(actions_home)

    # Read the Windows trust store before network activity; certificate verification stays on.
    roots = windows_roots()

    # Use the same RCC download helper as actions-runtime; this keeps its version aligned.
    sys.path.insert(0, str(ROOT / "src"))
    from actions.server import _download_rcc, _settings

    actions_home = _settings.get_actions_home()
    actions_home.mkdir(parents=True, exist_ok=True)
    rcc = _download_rcc.download_rcc()
    action_profile = prepare_profile(rcc, actions_home, "actions", roots)
    print(f"Prepared managed RCC {rcc}; active profile retained as {action_profile!r}.")
    print(f"Managed RCC home: {actions_home}")


if __name__ == "__main__":
    main()
