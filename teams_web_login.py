"""One-time headed sign-in helper; invoke only with operator coordination."""
import os
from pathlib import Path

from robocorp import browser
from playwright.sync_api import Error


def login() -> None:
    profile = Path(os.environ["LOCALAPPDATA"]) / "OfficeActionServer" / "teams-web-profile"
    profile.mkdir(parents=True, exist_ok=True)
    browser.configure(
        browser_engine="msedge",
        headless=False,
        persistent_context_directory=str(profile),
    )
    context = browser.context()
    try:
        # Keep the initial tab available if Teams closes its navigation tab.
        page = context.new_page()
        try:
            page.goto("https://teams.cloud.microsoft/", wait_until="domcontentloaded", timeout=20000)
        except Error as exc:
            print(f"Teams navigation failed ({type(exc).__name__}); keeping the isolated profile open.", flush=True)
            remaining = context.pages
            if remaining:
                try:
                    remaining[0].goto("https://login.microsoftonline.com/", wait_until="domcontentloaded", timeout=20000)
                except Error as fallback:
                    print(f"Microsoft sign-in navigation: {type(fallback).__name__}", flush=True)
        print("Complete sign-in in this isolated Edge window. Close its windows when finished.", flush=True)
        while context.pages:
            try:
                context.pages[0].wait_for_timeout(500)
            except Error:
                if not context.pages:
                    break
    finally:
        context.close()


if __name__ == "__main__":
    login()
