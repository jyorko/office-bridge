"""Launch the supported Actions Runtime CLI without secrets in process arguments."""
import os
from pathlib import Path

from actions.server.cli import main

ROOT = Path(__file__).resolve().parent
STATE = ROOT / ".state"

if __name__ == "__main__":
    os.chdir(ROOT)
    key = (STATE / ".api_key").read_text(encoding="utf-8").strip()
    if len(key) < 32:
        raise SystemExit("Missing local API key. Run start-office.ps1 first.")
    main([
        "start", "--address", "127.0.0.1", "--port", "8766",
        "--dir", str(ROOT / ".package"), "--datadir", str(STATE / "managed-server"),
        "--api-key", key, "--min-processes", "1", "--max-processes", "1",
    ])
