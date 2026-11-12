"""One-time headed sign-in helper; invoke only with operator coordination."""
import os
from pathlib import Path

from robocorp import browser


def login() -> None:
    profile = Path(os.environ["LOCALAPPDATA"]) / "OfficeActionServer" / "teams-web-profile"
    profile.mkdir(parents=True, exist_ok=True)
    browser.configure(
        browser_engine="msedge",
        headless=False,
        persistent_context_directory=str(profile),
    )
    context = None
    try:
        page = browser.page()
        context = page.context
        page.goto("https://teams.cloud.microsoft/", wait_until="domcontentloaded", timeout=20000)
        input("Complete sign-in in the isolated Teams browser, then press Enter: ")
    finally:
        if context is not None:
            context.close()


if __name__ == "__main__":
    login()
