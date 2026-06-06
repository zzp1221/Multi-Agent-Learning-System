from pathlib import Path

import pytest
from playwright.sync_api import Error as PlaywrightError, sync_playwright


SYSTEM_BROWSER_PATHS = (
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
)


@pytest.fixture
def page():
    with sync_playwright() as playwright:
        launch_options = {
            "headless": True,
            "args": ["--disable-gpu", "--disable-dev-shm-usage"],
        }
        try:
            browser = playwright.chromium.launch(**launch_options)
        except PlaywrightError as exc:
            fallback = next((path for path in SYSTEM_BROWSER_PATHS if path.exists()), None)
            if fallback is None or "Executable doesn't exist" not in str(exc):
                raise
            browser = playwright.chromium.launch(
                **launch_options,
                executable_path=str(fallback),
            )
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            ignore_https_errors=True,
        )
        page = context.new_page()
        try:
            yield page
        finally:
            context.close()
            browser.close()
