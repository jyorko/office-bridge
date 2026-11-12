"""One-time headed sign-in helper for the isolated Teams profile."""
import argparse

from robocorp import browser
from playwright.sync_api import Error

from teams_web_backend import BrowserEngine, TEAMS_URL, profile_lock


def login(engine: BrowserEngine = "chromium") -> None:
    with profile_lock(engine) as profile:
        browser.configure(
            browser_engine=engine,
            headless=False,
            persistent_context_directory=str(profile),
        )
        context = browser.context()
        try:
            page = context.new_page()
            try:
                page.goto(TEAMS_URL, wait_until="domcontentloaded", timeout=20000)
            except Error as exc:
                print(f"Teams navigation failed ({type(exc).__name__}); keeping the isolated profile open.", flush=True)
                remaining = context.pages
                if remaining:
                    try:
                        remaining[0].goto("https://login.microsoftonline.com/", wait_until="domcontentloaded", timeout=20000)
                    except Error as fallback:
                        print(f"Microsoft sign-in navigation: {type(fallback).__name__}", flush=True)
            print("Complete sign-in in this isolated browser window. Close its windows when finished.", flush=True)
            while context.pages:
                try:
                    context.pages[0].wait_for_timeout(500)
                except Error:
                    if not context.pages:
                        break
        finally:
            context.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser-engine", choices=("chromium", "msedge"), default="chromium")
    login(parser.parse_args().browser_engine)
