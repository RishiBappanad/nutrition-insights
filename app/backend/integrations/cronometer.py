"""
Cronometer CSV Download Automation using Playwright

This module handles logging into Cronometer and downloading daily nutrition CSVs.
"""

import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from playwright.async_api import async_playwright, Page, Browser, BrowserContext

logger = logging.getLogger(__name__)
load_dotenv()


class CronometerAutomation:
    """Handles automated Cronometer data extraction using Playwright."""

    CRONOMETER_URL = "https://cronometer.com"
    LOGIN_EMAIL_SELECTOR = "input[name='email']"
    LOGIN_PASSWORD_SELECTOR = "input[name='password']"
    LOGIN_BUTTON_SELECTOR = "button[type='submit']"
    EXPORT_MENU_SELECTOR = "[data-test='export-menu']"  # Adjust based on actual page structure
    DOWNLOAD_SERVINGS_SELECTOR = "[data-test='download-servings']"  # Adjust accordingly
    DOWNLOAD_SUMMARY_SELECTOR = "[data-test='download-summary']"

    def __init__(
        self,
        email: Optional[str] = None,
        password: Optional[str] = None,
        download_dir: str = "raw_data",
        headless: bool = True,
    ):
        """
        Initialize Cronometer automation.

        Args:
            email: Cronometer email (defaults to CRONOMETER_EMAIL env var)
            password: Cronometer password (defaults to CRONOMETER_PASSWORD env var)
            download_dir: Directory to save downloaded CSVs
            headless: Run browser in headless mode
        """
        self.email = email or os.getenv("CRONOMETER_EMAIL")
        self.password = password or os.getenv("CRONOMETER_PASSWORD")
        self.download_dir = Path(download_dir)
        self.headless = headless
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None

        if not self.email or not self.password:
            raise ValueError("Cronometer credentials not provided via arguments or environment variables")

        self.download_dir.mkdir(parents=True, exist_ok=True)

    async def __aenter__(self):
        """Async context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()

    async def start(self) -> None:
        """Start Playwright browser instance."""
        try:
            playwright = await async_playwright().start()
            self.browser = await playwright.chromium.launch(headless=self.headless)

            # Create context with download handler
            self.context = await self.browser.new_context(
                accept_downloads=True,
            )
            logger.info("Playwright browser started successfully")
        except Exception as e:
            logger.error(f"Failed to start Playwright: {e}")
            raise

    async def close(self) -> None:
        """Close Playwright browser and context."""
        try:
            if self.context:
                await self.context.close()
            if self.browser:
                await self.browser.close()
            logger.info("Playwright browser closed")
        except Exception as e:
            logger.error(f"Error closing browser: {e}")

    async def login(self, page: Page, max_retries: int = 3) -> bool:
        """
        Log into Cronometer.

        Args:
            page: Playwright Page object
            max_retries: Number of login attempts

        Returns:
            True if login successful, False otherwise
        """
        for attempt in range(max_retries):
            try:
                logger.info(f"Login attempt {attempt + 1}/{max_retries}")

                # Navigate to Cronometer
                await page.goto(self.CRONOMETER_URL, wait_until="networkidle")
                await asyncio.sleep(2)

                # Fill in email
                await page.fill(self.LOGIN_EMAIL_SELECTOR, self.email)
                logger.debug("Email field filled")

                # Fill in password
                await page.fill(self.LOGIN_PASSWORD_SELECTOR, self.password)
                logger.debug("Password field filled")

                # Click login button
                await page.click(self.LOGIN_BUTTON_SELECTOR)
                logger.debug("Login button clicked")

                # Wait for navigation after login
                await page.wait_for_load_state("networkidle", timeout=15000)

                # Verify login success by checking for a protected element
                is_logged_in = await page.query_selector("text=/Dashboard|My Foods|Export/i")

                if is_logged_in:
                    logger.info("Successfully logged into Cronometer")
                    return True
                else:
                    logger.warning("Login verification failed, retrying...")

            except Exception as e:
                logger.warning(f"Login attempt {attempt + 1} failed: {e}")
                await asyncio.sleep(2)

        logger.error("Failed to log into Cronometer after maximum retries")
        return False

    async def navigate_to_export(self, page: Page) -> bool:
        """
        Navigate to export section.

        Args:
            page: Playwright Page object

        Returns:
            True if navigation successful
        """
        try:
            # Try multiple selectors/paths to find export section
            export_selectors = [
                "text=/Export|Settings/i",
                "a[href*='export']",
                "[data-test='export-menu']",
            ]

            for selector in export_selectors:
                try:
                    element = await page.query_selector(selector)
                    if element:
                        await element.click()
                        await page.wait_for_load_state("networkidle", timeout=10000)
                        logger.info("Navigated to export section")
                        return True
                except Exception:
                    continue

            logger.error("Could not find export section with any selector")
            return False

        except Exception as e:
            logger.error(f"Error navigating to export: {e}")
            return False

    async def download_csv(
        self,
        page: Page,
        csv_type: str = "daily_summary",
    ) -> Optional[str]:
        """
        Download a specific CSV from Cronometer.

        Args:
            page: Playwright Page object
            csv_type: Type of CSV ('daily_summary' or 'servings')

        Returns:
            Path to downloaded file, or None if failed
        """
        try:
            # Map CSV types to selectors
            selector_map = {
                "daily_summary": self.DOWNLOAD_SUMMARY_SELECTOR,
                "servings": self.DOWNLOAD_SERVINGS_SELECTOR,
            }

            selector = selector_map.get(csv_type)
            if not selector:
                logger.error(f"Unknown CSV type: {csv_type}")
                return None

            # Start listening for download event
            async with page.expect_download() as download_info:
                await page.click(selector)

            download = await download_info.value

            # Generate timestamped filename
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"cronometer_{csv_type}_{timestamp}.csv"
            filepath = self.download_dir / filename

            # Save download
            await download.save_as(str(filepath))
            logger.info(f"Downloaded {csv_type} CSV to {filepath}")

            return str(filepath)

        except Exception as e:
            logger.error(f"Error downloading {csv_type} CSV: {e}")
            return None

    async def download_all_csvs(self) -> dict[str, Optional[str]]:
        """
        Main workflow: login and download all available CSVs.

        Returns:
            Dictionary mapping CSV types to file paths
        """
        if not self.context:
            raise RuntimeError("Browser context not initialized. Call start() first.")

        page = await self.context.new_page()

        try:
            results = {}

            # Step 1: Login
            if not await self.login(page):
                return {"error": "Login failed"}

            # Step 2: Navigate to export
            if not await self.navigate_to_export(page):
                return {"error": "Navigation to export failed"}

            # Step 3: Download CSVs
            results["daily_summary"] = await self.download_csv(page, "daily_summary")
            await asyncio.sleep(2)  # Rate limiting between downloads

            results["servings"] = await self.download_csv(page, "servings")

            return results

        except Exception as e:
            logger.error(f"Unexpected error during export: {e}")
            return {"error": str(e)}

        finally:
            await page.close()


async def main() -> None:
    """Example usage of CronometerAutomation."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    try:
        async with CronometerAutomation(headless=False) as automation:
            results = await automation.download_all_csvs()

            logger.info("Download Results:")
            for csv_type, filepath in results.items():
                if filepath:
                    logger.info(f"  {csv_type}: {filepath}")
                else:
                    logger.error(f"  {csv_type}: FAILED")

    except ValueError as e:
        logger.error(f"Configuration error: {e}")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
