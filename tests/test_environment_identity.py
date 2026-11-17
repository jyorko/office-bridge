"""Prove that managed tasks and action workers select one RCC environment."""

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RELEASE = json.loads((ROOT / "scripts/runtime-release.json").read_text())
RCC = (
    Path(os.environ.get("LOCALAPPDATA", ""))
    / "OfficeActionServer"
    / "bin"
    / f"rcc-runtime-{RELEASE['version']}.exe"
)


@pytest.mark.skipif(os.name != "nt" or not RCC.is_file(), reason="pinned Windows RCC required")
def test_tasks_and_workers_share_the_same_dependency_identity():
    arguments = [
        str(RCC),
        "holotree",
        "hash",
        str(ROOT / "package.yaml"),
        "--silent",
        "--no-temp-management",
        "--warranty-voided",
    ]
    worker = subprocess.check_output(arguments, text=True, timeout=30).strip()
    task = subprocess.check_output([*arguments, "--devdeps"], text=True, timeout=30).strip()
    assert worker
    assert task == worker, "Managed tasks and action workers must reuse one RCC environment."
