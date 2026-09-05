"""
Hevy Web Scraper - Exports workout history via Playwright browser automation.
Logs into hevy.com and triggers the CSV export from Settings > Export Data.
"""

import os
import logging
from typing import Optional

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

logger = logging.getLogger(__name__)


class HevyWebScraper:
    """Exports Hevy workout data by automating the web CSV export."""

    LOGIN_URL = "https://hevy.com/login"
    EXPORT_URL = "https://hevy.com/settings?export"

    def __init__(self, headless: bool = True):
        if not PLAYWRIGHT_AVAILABLE:
            raise ImportError("Playwright required. Install with: pip install playwright")
        self.headless = headless
        self._playwright = None
        self._browser = None
        self._page = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.stop()

    def start(self):
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=self.headless)
        self._page = self._browser.new_page()

    def stop(self):
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()

    def login(self, username: str, password: str) -> bool:
        """Login to Hevy. Returns True on success."""
        try:
            self._page.goto(self.LOGIN_URL, wait_until="networkidle")
            self._page.wait_for_timeout(2000)

            inputs = self._page.locator("input").all()
            inputs[0].fill(username)
            inputs[1].fill(password)

            self._page.get_by_role("button", name="Login", exact=True).click()
            self._page.wait_for_timeout(5000)

            success = "/login" not in self._page.url
            if success:
                logger.info("Hevy login successful")
            else:
                logger.error("Hevy login failed - still on login page")
            return success
        except Exception as e:
            logger.error(f"Hevy login error: {e}")
            return False

    def export_workouts(self, output_dir: str = "raw_data") -> Optional[str]:
        """Navigate to export page, click export, save CSV. Returns file path or None."""
        try:
            os.makedirs(output_dir, exist_ok=True)

            self._page.goto(self.EXPORT_URL, wait_until="networkidle")
            self._page.wait_for_timeout(2000)

            with self._page.expect_download(timeout=30000) as download_info:
                self._page.get_by_role("button", name="Export Workout Data").click()

            download = download_info.value
            save_path = os.path.join(output_dir, "hevy_workouts.csv")
            download.save_as(save_path)

            logger.info(f"Exported Hevy workouts to {save_path}")
            return save_path
        except Exception as e:
            logger.error(f"Hevy export failed: {e}")
            return None


def export_hevy_data(headless: bool = True, output_dir: str = "raw_data") -> Optional[str]:
    """Convenience function: login and export in one call. Uses env vars for credentials."""
    from dotenv import load_dotenv
    load_dotenv()

    username = os.getenv("HEVY_USERNAME")
    password = os.getenv("HEVY_PASSWORD")
    if not username or not password:
        logger.error("HEVY_USERNAME and HEVY_PASSWORD env vars required")
        return None

    with HevyWebScraper(headless=headless) as scraper:
        if not scraper.login(username, password):
            return None
        return scraper.export_workouts(output_dir)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    path = export_hevy_data(headless=False)
    if path:
        print(f"✓ Exported to: {path}")
    else:
        print("✗ Export failed")
