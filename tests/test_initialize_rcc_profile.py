import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
RELEASE = json.loads((ROOT / "scripts/runtime-release.json").read_text())
RCC = (
    Path(os.environ.get("LOCALAPPDATA", ""))
    / "OfficeActionServer"
    / "bin"
    / f"rcc-runtime-{RELEASE['version']}.exe"
)


@pytest.mark.skipif(os.name != "nt", reason="RCC bootstrap profile editing uses Windows PowerShell")
def test_rcc_profile_merge_preserves_settings_and_deduplicates_certificates():
    script = Path(__file__).with_name("test_initialize_rcc_profile.ps1")
    subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.mark.skipif(os.name != "nt" or not RCC.is_file(), reason="pinned Windows RCC required")
def test_native_bootstrap_handles_fresh_and_custom_application_homes(tmp_path):
    project = tmp_path / "project"
    for relative in (
        "powershell/initialize-rcc.ps1",
        "src/office_bridge/native/rcc-trust.ps1",
        "scripts/runtime-release.json",
    ):
        destination = project / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)
    home = tmp_path / "actions-home"
    env = dict(os.environ, ACTIONS_HOME=str(home), ROBOCORP_HOME=str(home))

    def bootstrap():
        subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(project / "powershell/initialize-rcc.ps1"),
            ],
            env=env,
            check=True,
            capture_output=True,
            text=True,
            timeout=180,
        )

    def rcc(*arguments):
        return subprocess.check_output([str(RCC), *arguments], env=env, text=True, timeout=30)

    def export(profile):
        destination = tmp_path / "exported.yaml"
        rcc("configuration", "export", "--profile", profile, "--filename", str(destination))
        return yaml.safe_load(destination.read_text())

    bootstrap()
    fresh = export("default")
    assert fresh["settings"]["certificates"]["verify-ssl"] is True
    assert fresh["settings"]["certificates"]["ssl-no-revoke"] is True
    assert fresh["ca-bundle"].count("-----BEGIN CERTIFICATE-----") >= 10

    fresh["name"] = "custom-profile"
    fresh["description"] = "Existing profile: retain its settings."
    fresh["settings"]["meta"].update(name="custom-profile", source="test fixture")
    fresh["settings"]["certificates"].update(
        {"verify-ssl": False, "ssl-no-revoke": False, "legacy-renegotiation-allowed": True}
    )
    custom_path = tmp_path / "custom.yaml"
    custom_path.write_text(yaml.safe_dump(fresh))
    rcc("configuration", "import", "--filename", str(custom_path), "--switch")
    original = export("custom-profile")

    bootstrap()
    retained = export("custom-profile")
    assert retained["description"] == original["description"]
    assert retained["settings"]["meta"] == original["settings"]["meta"]
    certificates = retained["settings"]["certificates"]
    assert certificates["verify-ssl"] is True
    assert certificates["ssl-no-revoke"] is True
    assert certificates["legacy-renegotiation-allowed"] is True
    assert retained["ca-bundle"] == original["ca-bundle"]
    diagnostics = json.loads(rcc("configuration", "diagnostics", "--quick", "--json"))
    assert diagnostics["details"]["config-active-profile"] == "custom-profile"
    assert not any((home / "holotree").glob("*"))
