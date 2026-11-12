"""Launch the supported Actions Runtime CLI without secrets in process arguments."""
import os
import shutil
import sys
import threading
from pathlib import Path

from actions.server.cli import main

ROOT = Path(__file__).resolve().parent
STATE = ROOT / ".state"


def watch_sources():
    from watchfiles import watch

    staged = ROOT / ".package"
    authored = {
        ROOT / path.relative_to(staged)
        for path in staged.rglob("*")
        if path.is_file() and path.suffix in {".py", ".yaml", ".ps1", ".js"}
    }
    for changes in watch(
        ROOT, ROOT / "powershell", recursive=False,
        watch_filter=lambda _, path: Path(path) in authored,
    ):
        for _, path in changes:
            source = Path(path)
            if source.is_file():
                shutil.copyfile(source, staged / source.relative_to(ROOT))


if __name__ == "__main__":
    os.chdir(ROOT)
    dev = "--dev" in sys.argv[1:]
    key = "" if dev else (STATE / ".api_key").read_text(encoding="utf-8").strip()
    if not dev and len(key) < 32:
        raise SystemExit("Missing local API key. Run start-office.ps1 first.")
    if dev:
        threading.Thread(target=watch_sources, daemon=True).start()
    main([
        "start", "--address", "127.0.0.1", "--port", "8766",
        "--dir", str(ROOT / ".package"), "--datadir", str(STATE / "managed-server"),
        "--min-processes", "1", "--max-processes", "1", "--full-openapi-spec",
    ] + (["--auto-reload"] if dev else ["--api-key", key]))
