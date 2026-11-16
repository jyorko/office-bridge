import os
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright


@pytest.fixture(scope="session")
def isolated_browser():
    executable = (
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "robocorp/playwright/chromium-1254/chrome-win64/chrome.exe"
    )
    if not executable.is_file():
        pytest.skip("isolated Chromium test browser is not installed")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=str(executable), headless=True)
        yield browser
        browser.close()
