"""
Cronometer Web Scraper - Exports data via Playwright browser automation.
Logs into cronometer.com and triggers CSV exports from the Settings/Export page.
"""

import os
import csv
import logging
from typing import Optional, Dict
from pathlib import Path
from datetime import datetime

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

logger = logging.getLogger(__name__)


class CronometerWebScraper:
    """Exports Cronometer data by automating the web CSV export."""

    BASE_URL = "https://cronometer.com"
    LOGIN_URL = "https://cronometer.com/login/"
    # The export page is accessible after login at this path
    EXPORT_URL = "https://cronometer.com/#export"

    def __init__(self, headless: bool = True):
        if not PLAYWRIGHT_AVAILABLE:
            raise ImportError("Playwright required. Install with: pip install playwright && playwright install chromium")
        self.headless = headless
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.stop()

    def start(self):
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=self.headless)
        self._context = self._browser.new_context(accept_downloads=True)
        self._page = self._context.new_page()

    def stop(self):
        if self._context:
            self._context.close()
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()

    def login(self, username: str, password: str) -> bool:
        """Login to Cronometer. Returns True on success."""
        try:
            self._page.goto(self.LOGIN_URL, wait_until="networkidle")
            self._page.wait_for_timeout(2000)

            # Fill login form
            self._page.fill("input[name='username']", username)
            self._page.fill("input[name='password']", password)
            self._page.click("input[type='submit'], button[type='submit']")
            self._page.wait_for_timeout(5000)

            # Check if we're logged in by looking for the app page
            if "login" in self._page.url.lower():
                # Try alternate login flow
                logger.warning("First login attempt may have failed, checking page state...")
                return False

            logger.info("Cronometer login successful")
            return True
        except Exception as e:
            logger.error(f"Cronometer login error: {e}")
            return False

    def export_all(self, start_date: str, end_date: str, output_dir: str = "raw_data") -> Dict[str, Optional[str]]:
        """
        Navigate to export page, set date range, and download all CSV types.
        
        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
            output_dir: Directory to save files
            
        Returns:
            Dict mapping export type to file path (or None if failed)
        """
        os.makedirs(output_dir, exist_ok=True)
        results = {}

        # Navigate to the export section
        try:
            self._page.goto(self.EXPORT_URL, wait_until="networkidle")
            self._page.wait_for_timeout(3000)
        except Exception as e:
            logger.error(f"Failed to navigate to export page: {e}")
            return results

        # Set date range - Cronometer uses date inputs
        try:
            # Look for date range inputs
            start_inputs = self._page.locator("input[type='date']").all()
            if len(start_inputs) >= 2:
                start_inputs[0].fill(start_date)
                start_inputs[1].fill(end_date)
                self._page.wait_for_timeout(1000)
            else:
                # Try text inputs for dates
                date_inputs = self._page.locator("input.gwt-DateBox, input[placeholder*='date'], input[aria-label*='date']").all()
                if len(date_inputs) >= 2:
                    date_inputs[0].fill(start_date)
                    date_inputs[1].fill(end_date)
                    self._page.wait_for_timeout(1000)
        except Exception as e:
            logger.warning(f"Could not set date range: {e}")

        # Export types to download
        export_types = [
            ("daily_summary", "Daily Nutrition"),
            ("servings", "Servings"),
            ("exercises", "Exercises"),
            ("biometrics", "Biometrics"),
        ]

        for export_key, button_text in export_types:
            try:
                filepath = self._download_export(button_text, export_key, output_dir)
                results[export_key] = filepath
            except Exception as e:
                logger.error(f"Failed to export {export_key}: {e}")
                results[export_key] = None

        return results

    def _download_export(self, button_text: str, export_key: str, output_dir: str) -> Optional[str]:
        """Click an export button and save the downloaded file."""
        try:
            # Try to find and click the export button by text
            button = self._page.get_by_role("button", name=button_text)
            if not button.is_visible():
                # Try finding by link text
                button = self._page.get_by_text(button_text, exact=False)

            with self._page.expect_download(timeout=30000) as download_info:
                button.click()

            download = download_info.value
            save_path = os.path.join(output_dir, f"cronometer_{export_key}.csv")
            download.save_as(save_path)
            logger.info(f"Exported {export_key} to {save_path}")
            return save_path

        except Exception as e:
            logger.error(f"Download failed for {export_key}: {e}")
            return None
