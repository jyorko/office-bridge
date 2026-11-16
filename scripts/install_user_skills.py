"""Install Office Bridge skills in the shared current-user agent skill directory."""

from pathlib import Path

from _bootstrap import ROOT

from office_bridge.runtime.skills import install

if __name__ == "__main__":
    for path in install(ROOT, Path.home() / ".agents" / "skills"):
        print(f"Installed user skill: {path}")
