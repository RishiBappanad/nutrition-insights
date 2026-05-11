"""
Hevy Web Scraper - Direct web scraping client for Hevy workout data
Scrapes hevy.com web interface instead of using paid API endpoints
"""

import requests
import json
import time
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
import logging
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
    from playwright.sync_api import Page, Browser, BrowserContext
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

logger = logging.getLogger(__name__)

class HevyWebScraper:
    """
    Web scraper for Hevy workout data using Playwright browser automation.
    Scrapes hevy.com web interface directly without requiring API subscription.
    """
    
    HEVY_URL = "https://hevy.com"
    
    def __init__(self, headless: bool = True):
        """
        Initialize Hevy web scraper.
        
        Args:
            headless: Whether to run browser in headless mode
        """
        if not PLAYWRIGHT_AVAILABLE:
            raise ImportError("Playwright is required for Hevy web scraping. Install with: pip install playwright")
        
        self.headless = headless
        self.browser = None
        self.context = None
        self.page = None
        self.last_login_attempt = 0
        self.min_login_interval = 300  # 5 minutes between login attempts
        
    def __enter__(self):
        """Context manager entry."""
        self.start_browser()
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.stop_browser()
        
    def start_browser(self):
        """Start browser and create page."""
        try:
            self.playwright = sync_playwright().start()
            self.browser = self.playwright.chromium.launch(headless=self.headless)
            self.context = self.browser.new_context(
                viewport={'width': 1280, 'height': 720},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            )
            self.page = self.context.new_page()
            logger.info("Browser started successfully")
        except Exception as e:
            logger.error(f"Failed to start browser: {e}")
            raise
            
    def stop_browser(self):
        """Stop browser and cleanup."""
        try:
            if self.page:
                self.page.close()
            if self.context:
                self.context.close()
            if self.browser:
                self.browser.close()
            if hasattr(self, 'playwright'):
                self.playwright.stop()
            logger.info("Browser stopped successfully")
        except Exception as e:
            logger.error(f"Error stopping browser: {e}")
    
    def _check_rate_limit(self) -> bool:
        """Check if we're rate limited and wait if necessary."""
        current_time = time.time()
        time_since_last = current_time - self.last_login_attempt
        
        if time_since_last < self.min_login_interval:
            wait_time = self.min_login_interval - time_since_last
            logger.warning(f"Rate limiting detected. Waiting {wait_time:.1f} seconds before attempting login...")
            time.sleep(wait_time)
            return True
        return False
    
    def login(self, email_or_username: str, password: str) -> bool:
        """
        Login to Hevy web interface.
        
        Args:
            email_or_username: User's email or username
            password: User's password
            
        Returns:
            True if login successful, False otherwise
        """
        try:
            # Check rate limiting
            self._check_rate_limit()
            self.last_login_attempt = time.time()
            
            logger.info(f"Navigating to {self.HEVY_URL}")
            self.page.goto(self.HEVY_URL, wait_until='networkidle')
            self.page.wait_for_timeout(3000)
            
            # Check if we're already logged in
            if self._is_logged_in():
                logger.info("Already logged in")
                return True
            
            # Look for login form using CSS classes from debug output
            email_input = self.page.locator('.sc-2dbec87c-2.qBoLv').first  # First input (email)
            password_input = self.page.locator('input[type="password"]').first  # Second input (password)
            login_button = self.page.locator('.sc-a84253f4-0.bxxRNZ').first  # Login button
            
            # Fill in credentials
            logger.info("Filling login form")
            email_input.fill(email_or_username)
            password_input.fill(password)
            
            # Submit login form using Enter key on password field
            logger.info("Submitting login form via Enter key")
            
            try:
                password_input.press('Enter')
                self.page.wait_for_timeout(5000)
                
                # Check if login successful
                if self._is_logged_in():
                    logger.info("Login successful")
                    return True
                else:
                    # Check for error messages
                    error_element = self.page.locator('[class*="error"], [data-testid="error"], .error-message').first
                    if error_element.is_visible():
                        error_text = error_element.text_content()
                        logger.error(f"Login failed: {error_text}")
                        
                        # Check for rate limiting indicators
                        rate_limit_indicators = [
                            "too many attempts",
                            "rate limit",
                            "try again later",
                            "temporarily blocked",
                            "security measure"
                        ]
                        
                        if any(indicator in error_text.lower() for indicator in rate_limit_indicators):
                            logger.warning("Rate limiting detected. Setting longer wait time.")
                            self.min_login_interval = 1800  # 30 minutes for rate limiting
                            self.last_login_attempt = time.time()
                            
                    else:
                        logger.error("Login failed - unknown reason")
                    return False
            except Exception as e:
                logger.error(f"Login submission failed: {e}")
                return False
                
        except Exception as e:
            logger.error(f"Login error: {e}")
            return False
            
    def _is_logged_in(self) -> bool:
        """Check if user is logged in."""
        try:
            # Check URL for logged-in patterns - this is most reliable
            current_url = self.page.url
            
            # If we're on the main hevy.com page (not login), we're logged in
            if current_url == "https://hevy.com/" or current_url == "https://hevy.com":
                return True
                
            # Other logged-in URL patterns
            logged_in_patterns = ['/dashboard', '/workouts', '/profile', '/feed', '/app']
            if any(pattern in current_url for pattern in logged_in_patterns):
                return True
                
            # Look for elements that indicate logged in state
            logged_in_indicators = [
                '[data-testid="user-menu"]',
                '.user-profile',
                '[href*="profile"]',
                '[href*="settings"]',
                '.dashboard',
                '.workout-list',
                '[data-testid="feed"]',
                '.feed',
                '.workout-card'
            ]
            
            for indicator in logged_in_indicators:
                element = self.page.locator(indicator).first
                if element.is_visible(timeout=2000):
                    return True
                
            return False
        except:
            return False
    
    def get_workouts(self, limit: int = 50) -> Optional[List[Dict[str, Any]]]:
        """
        Scrape workout data from Hevy web interface.
        
        Args:
            limit: Maximum number of workouts to retrieve
            
        Returns:
            List of workout data or None if failed
        """
        try:
            logger.info("Navigating to profile page for export options")
            
            # Step 1: Click "See your profile" button to access profile/settings
            profile_button = self.page.locator('button:has-text("See your profile")').first
            if profile_button.is_visible(timeout=3000):
                logger.info("Clicking profile button")
                profile_button.click()
                self.page.wait_for_timeout(3000)
            else:
                logger.warning("Profile button not found, trying direct navigation")
                self.page.goto(f"{self.HEVY_URL}/profile", wait_until='networkidle')
                self.page.wait_for_timeout(3000)
            
            # Step 2: Look for export/data options on profile page
            logger.info("Looking for export options on profile page")
            current_url = self.page.url
            logger.info(f"Current URL: {current_url}")
            
            # Try to find export/download buttons
            export_selectors = [
                'button:has-text("Export")',
                'button:has-text("Download")',
                'a:has-text("Export")',
                'a:has-text("Download")',
                'button:has-text("CSV")',
                'a:has-text("CSV")',
                '[data-testid="export"]',
                '[data-testid="download"]',
                '.export',
                '.download'
            ]
            
            export_found = False
            for selector in export_selectors:
                try:
                    elements = self.page.locator(selector).all()
                    if elements:
                        logger.info(f"Found export elements: {selector} ({len(elements)} items)")
                        export_found = True
                        break
                except:
                    continue
            
            if export_found:
                # Step 3: Try to export data
                logger.info("Attempting to export workout data")
                
                # Set up download handler
                with self.page.expect_download(timeout=15000) as download_info:
                    # Try different export approaches
                    export_approaches = [
                        ("CSV export", 'button:has-text("CSV")'),
                        ("Export button", 'button:has-text("Export")'),
                        ("Download button", 'button:has-text("Download")'),
                    ]
                    
                    export_success = False
                    for approach_name, selector in export_approaches:
                        try:
                            export_button = self.page.locator(selector).first
                            if export_button.is_visible(timeout=2000):
                                logger.info(f"Clicking {approach_name}")
                                export_button.click()
                                
                                # Wait for download
                                download = download_info.value
                                filename = download.suggested_filename
                                save_path = os.path.join('raw_data', filename)
                                os.makedirs('raw_data', exist_ok=True)
                                download.save_as(save_path)
                                logger.info(f"Exported: {filename}")
                                export_success = True
                                break
                        except Exception as e:
                            logger.warning(f"Export approach {approach_name} failed: {e}")
                            continue
                    
                    if export_success:
                        logger.info("Workout data exported successfully")
                        return self._parse_exported_file(save_path)
                    else:
                        logger.warning("All export approaches failed")
            
            # Step 4: If export not available, try to scrape workout data directly
            logger.info("Export not available, scraping workout data directly")
            
            # Try to navigate to workouts page
            workout_urls = [
                f"{self.HEVY_URL}/workouts",
                f"{self.HEVY_URL}/feed", 
                f"{self.HEVY_URL}/dashboard",
            ]
            
            workouts_loaded = False
            for url in workout_urls:
                try:
                    self.page.goto(url, wait_until='networkidle')
                    self.page.wait_for_timeout(3000)
                    
                    # Check if workout list is visible
                    workout_selectors = [
                        '[data-testid="workout-list"]',
                        '.workout-list',
                        '[data-testid="feed"]',
                        '.feed',
                        '.workout-card',
                        '[class*="workout"]'
                    ]
                    
                    for selector in workout_selectors:
                        workout_container = self.page.locator(selector).first
                        if workout_container.is_visible(timeout=3000):
                            workouts_loaded = True
                            logger.info(f"Found workouts at {url}")
                            break
                            
                    if workouts_loaded:
                        break
                        
                except Exception as e:
                    logger.warning(f"Failed to load workouts from {url}: {e}")
                    continue
                    
            if not workouts_loaded:
                logger.error("Could not find workout list on any page")
                return None
                
            # Scroll to load more workouts
            logger.info("Loading workouts by scrolling")
            for _ in range(5):  # Scroll 5 times
                self.page.keyboard.press('End')
                self.page.wait_for_timeout(2000)
                
            # Extract workout data
            workouts = self._extract_workout_data()
            
            # Limit results if specified
            if limit and len(workouts) > limit:
                workouts = workouts[:limit]
                
            logger.info(f"Extracted {len(workouts)} workouts")
            return workouts
            
        except Exception as e:
            logger.error(f"Failed to get workouts: {e}")
            return None
    
    def _extract_workout_data(self) -> List[Dict[str, Any]]:
        """Extract workout data from current page."""
        try:
            workouts = []
            
            # Look for workout cards
            workout_selectors = [
                '[data-testid="workout-list"]',
                '.workout-list',
                '[data-testid="feed"]',
                '.feed',
                '.workout-card',
                '[class*="workout"]'
            ]
            
            for selector in workout_selectors:
                workout_elements = self.page.locator(selector).all()
                if workout_elements:
                    logger.info(f"Found {len(workout_elements)} workouts with selector: {selector}")
                    
                    for i, element in enumerate(workout_elements):
                        try:
                            workout_data = self._extract_single_workout(element, i)
                            if workout_data:
                                workouts.append(workout_data)
                        except Exception as e:
                            logger.warning(f"Failed to extract workout {i}: {e}")
                            continue
                    break
                    
            return workouts
            
        except Exception as e:
            logger.error(f"Error extracting workout data: {e}")
            return []
    
    def _extract_single_workout(self, element, index: int) -> Optional[Dict[str, Any]]:
        """Extract data from a single workout element."""
        try:
            # Get workout name
            name_selectors = [
                '[data-testid="workout-name"]',
                '.workout-name',
                'h1, h2, h3, h4',
                '[class*="title"]'
            ]
            
            workout_name = "Unknown Workout"
            for selector in name_selectors:
                name_element = element.locator(selector).first
                if name_element.is_visible(timeout=1000):
                    workout_name = name_element.text_content().strip()
                    break
            
            # Get workout date
            date_selectors = [
                '[data-testid="workout-date"]',
                '.workout-date',
                '[class*="date"]',
                'time'
            ]
            
            workout_date = datetime.now().strftime('%Y-%m-%d')
            for selector in date_selectors:
                date_element = element.locator(selector).first
                if date_element.is_visible(timeout=1000):
                    date_text = date_element.text_content().strip()
                    workout_date = self._parse_date(date_text)
                    break
            
            # Get exercise count
            exercise_count = 0
            
            # Look for exercises within the workout
            exercise_selectors = [
                '[data-testid="exercise-list"]',
                '.exercise-list',
                '[class*="exercise"]'
            ]
            
            exercises = []
            for selector in exercise_selectors:
                exercise_elements = element.locator(selector).all()
                if exercise_elements:
                    exercise_count = len(exercise_elements)
                    break
            
            return {
                'id': f"workout_{index}_{int(time.time())}",
                'name': workout_name,
                'date': workout_date,
                'start_time': int(time.time()),
                'end_time': int(time.time()) + 3600,  # Placeholder
                'exercise_count': exercise_count,
                'exercises': exercises,
                'estimated_volume_kg': 0,  # Placeholder
                'notes': '',
                'index': index
            }
            
        except Exception as e:
            logger.error(f"Error extracting single workout: {e}")
            return None
    
    def _parse_date(self, date_text: str) -> str:
        """Parse date text and return in YYYY-MM-DD format."""
        try:
            # Common date formats
            date_patterns = [
                r'(\d{1,2})/(\d{1,2})/(\d{4})',  # MM/DD/YYYY or M/D/YYYY
                r'(\d{4})-(\d{1,2})-(\d{1,2})',  # YYYY-MM-DD
                r'(\w+)\s+(\d{1,2}),\s+(\d{4})',  # Month DD, YYYY
            ]
            
            for pattern in date_patterns:
                match = re.search(pattern, date_text)
                if match:
                    groups = match.groups()
                    if len(groups) == 3:
                        month, day, year = groups
                        return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
                    elif len(groups) == 2:
                        month, day, year = groups
                        return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
                        
            # Default to today
            return datetime.now().strftime('%Y-%m-%d')
            
        except:
            return datetime.now().strftime('%Y-%m-%d')
    
    def _parse_exported_file(self, file_path: str) -> Dict[str, Any]:
        """Parse exported file and return structured data."""
        try:
            with open(file_path, 'r') as f:
                if file_path.endswith('.csv'):
                    # Parse CSV file
                    import csv
                    reader = csv.DictReader(f)
                    workouts = []
                    for row in reader:
                        workouts.append(row)
                    return {'workouts': workouts}
                elif file_path.endswith('.json'):
                    # Parse JSON file
                    data = json.load(f)
                    return {'workouts': data}
                else:
                    return {'raw': file_path}
        except Exception as e:
            logger.error(f"Failed to parse exported file: {e}")
            return {'error': str(e)}
    
    def export_workouts_to_csv_format(self, workouts: List[Dict[str, Any]]) -> Dict[str, List[str]]:
        """
        Convert workout data to CSV-like format for integration with existing pipeline.
        
        Args:
            workouts: List of workout dictionaries
            
        Returns:
            Dictionary with workouts and exercises data
        """
        workout_data = []
        exercise_data = []
        
        for workout in workouts:
            # Create workout summary
            workout_row = {
                'id': workout.get('id', ''),
                'date': workout.get('date', ''),
                'name': workout.get('name', ''),
                'start_time': workout.get('start_time', 0),
                'end_time': workout.get('end_time', 0),
                'duration_seconds': workout.get('end_time', 0) - workout.get('start_time', 0),
                'estimated_volume_kg': workout.get('estimated_volume_kg', 0),
                'exercise_count': workout.get('exercise_count', 0),
            }
            workout_data.append(workout_row)
            
            # Add exercise details
            exercises = workout.get('exercises', [])
            for exercise in exercises:
                exercise_row = {
                    'workout_id': workout.get('id', ''),
                    'workout_date': workout.get('date', ''),
                    'workout_name': workout.get('name', ''),
                    'exercise_title': exercise.get('title', ''),
                    'exercise_template_id': exercise.get('exercise_template_id', ''),
                    'muscle_group': exercise.get('muscle_group', ''),
                    'equipment_category': exercise.get('equipment_category', ''),
                }
                
                # Add set data
                sets = exercise.get('sets', [])
                for set_data in sets:
                    set_row = {
                        'workout_id': workout.get('id', ''),
                        'workout_date': workout.get('date', ''),
                        'workout_name': workout.get('name', ''),
                        'exercise_title': exercise.get('title', ''),
                        'set_index': set_data.get('index', 0),
                        'weight_kg': set_data.get('weight_kg', 0),
                        'reps': set_data.get('reps', 0),
                        'duration_seconds': set_data.get('duration_seconds', 0),
                        'distance_meters': set_data.get('distance_meters', 0),
                        'rpe': set_data.get('rpe', ''),
                    }
                    exercise_data.append(set_row)
            
        return {
            'workouts': workout_data,
            'exercises': exercise_data,
        }
    
    def export_all_to_files(self, start_date: datetime = None, end_date: datetime = None) -> Dict[str, str]:
        """
        Export all workout data to JSON files.
        
        Args:
            start_date: Optional start date for filtering
            end_date: Optional end date for filtering
            
        Returns:
            Dictionary mapping data types to file paths
        """
        try:
            # Get workouts
            workouts = self.get_workouts()
            
            if not workouts:
                logger.error("No workouts retrieved from Hevy")
                return {'error': 'No workouts found'}
            
            # Filter by date range if provided
            if start_date and end_date:
                filtered_workouts = []
                start_timestamp = int(start_date.timestamp())
                end_timestamp = int(end_date.timestamp())
                
                for workout in workouts:
                    workout_time = workout.get('start_time', 0)
                    if start_timestamp <= workout_time <= end_timestamp:
                        filtered_workouts.append(workout)
                        
                workouts = filtered_workouts
                logger.info(f"Filtered to {len(workouts)} workouts in date range")
            
            # Convert to CSV format
            csv_data = self.export_workouts_to_csv_format(workouts)
            
            # Save to files
            import os
            output_dir = 'raw_data'
            os.makedirs(output_dir, exist_ok=True)
            
            files = {}
            
            # Save workouts summary
            workouts_file = os.path.join(output_dir, 'hevy_workouts.json')
            with open(workouts_file, 'w') as f:
                json.dump(csv_data.get('workouts', []), f, indent=2)
            files['workouts'] = workouts_file
            
            # Save exercises details
            exercises_file = os.path.join(output_dir, 'hevy_exercises.json')
            with open(exercises_file, 'w') as f:
                json.dump(csv_data.get('exercises', []), f, indent=2)
            files['exercises'] = exercises_file
            
            # Save raw scraped data
            raw_file = os.path.join(output_dir, 'hevy_raw_workouts.json')
            with open(raw_file, 'w') as f:
                json.dump(workouts, f, indent=2)
            files['raw'] = raw_file
            
            logger.info(f"Exported Hevy data to {len(files)} files")
            return files
            
        except Exception as e:
            logger.error(f"Failed to export Hevy data: {e}")
            return {'error': str(e)}


def create_hevy_scraper() -> HevyWebScraper:
    """Create and return a Hevy web scraper instance."""
    return HevyWebScraper()


def create_hevy_client():
    """Create and return a Hevy RPC client instance."""
    return HevyRPCClient()


# Test function
def test_hevy_scraper():
    """Test Hevy web scraper with environment variables."""
    import os
    from dotenv import load_dotenv
    
    load_dotenv()
    
    username = os.getenv('HEVY_USERNAME')
    password = os.getenv('HEVY_PASSWORD')
    
    if not username or not password:
        print("HEVY_USERNAME and HEVY_PASSWORD environment variables required")
        return
    
    print("Testing Hevy web scraper...")
    
    with create_hevy_scraper() as scraper:
        if scraper.login(username, password):
            print("✓ Login successful")
            
            workouts = scraper.get_workouts(limit=3)
            if workouts:
                print(f"✓ Retrieved {len(workouts)} workouts")
                
                files = scraper.export_all_to_files()
                if 'error' not in files:
                    print(f"✓ Exported data to {len(files)} files")
                    for data_type, file_path in files.items():
                        print(f"  {data_type}: {file_path}")
                else:
                    print(f"✗ Export failed: {files['error']}")
            else:
                print("✗ Failed to retrieve workouts")
        else:
            print("✗ Login failed")


if __name__ == "__main__":
    test_hevy_scraper()
