"""
Hevy RPC Client - Direct API client for Hevy workout data
Reverse-engineered from Hevy's internal API endpoints
"""

import requests
import json
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
import logging

logger = logging.getLogger(__name__)

class HevyRPCClient:
    """
    Direct API client for Hevy workout data using internal endpoints.
    Similar approach to Cronometer RPC client but for Hevy's API.
    """
    
    # Hevy API endpoints
    BASE_URL = "https://api.hevyapp.com"
    LOGIN_URL = f"{BASE_URL}/login"
    WORKOUTS_URL = f"{BASE_URL}/feed_workouts_paged"
    
    # API keys for different endpoints (from reverse engineering)
    LOGIN_API_KEY = "shelobs_hevy_web"
    WORKOUTS_API_KEY = "klean_kanteen_insulated"
    
    def __init__(self):
        self.session = requests.Session()
        self.auth_token = None
        self.user_id = None
        self.last_request_time = 0
        self.min_request_interval = 2.0  # Minimum 2 seconds between requests
    
    def _rate_limit_wait(self):
        """Wait if necessary to respect rate limits."""
        current_time = time.time()
        time_since_last = current_time - self.last_request_time
        if time_since_last < self.min_request_interval:
            sleep_time = self.min_request_interval - time_since_last
            logger.info(f"Rate limiting: waiting {sleep_time:.1f} seconds")
            time.sleep(sleep_time)
        self.last_request_time = time.time()
    
    def _make_request_with_retry(self, method: str, url: str, **kwargs) -> requests.Response:
        """Make HTTP request with retry logic for rate limiting."""
        max_retries = 3
        base_delay = 1.0
        
        for attempt in range(max_retries):
            self._rate_limit_wait()
            
            try:
                response = self.session.request(method, url, **kwargs)
                
                if response.status_code == 429:
                    if attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt)
                        logger.warning(f"Rate limited (429), retry {attempt + 1}/{max_retries} after {delay}s")
                        time.sleep(delay)
                        continue
                    else:
                        logger.error("Max retries reached for rate limiting")
                        response.raise_for_status()
                else:
                    response.raise_for_status()
                    return response
                    
            except requests.exceptions.RequestException as e:
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    logger.warning(f"Request failed, retry {attempt + 1}/{max_retries} after {delay}s: {e}")
                    time.sleep(delay)
                    continue
                else:
                    logger.error(f"Max retries reached for request: {e}")
                    raise
        
    def login(self, email_or_username: str, password: str) -> bool:
        """
        Authenticate with Hevy API using email/username and password.
        
        Args:
            email_or_username: User's email or username
            password: User's password
            
        Returns:
            True if login successful, False otherwise
        """
        try:
            headers = {
                'Accept': 'application/json, text/plain, */*',
                'Accept-Language': 'en-GB,en-US;q=0.9,en;q=0.8',
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive',
                'Content-Type': 'application/json',
                'DNT': '1',
                'Origin': 'https://www.hevy.com',
                'Pragma': 'no-cache',
                'Referer': 'https://www.hevy.com/',
                'Sec-Fetch-Dest': 'empty',
                'Sec-Fetch-Mode': 'cors',
                'Sec-Fetch-Site': 'cross-site',
                'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/111.0.0.0 Safari/537.36',
                'sec-ch-ua': '"Google Chrome";v="111", "Not(A:Brand";v="8", "Chromium";v="111"',
                'sec-ch-ua-mobile': '?0',
                'sec-ch-ua-platform': '"Linux"',
                'x-api-key': self.LOGIN_API_KEY,
            }
            
            payload = {
                'emailOrUsername': email_or_username,
                'password': password,
            }
            
            response = self._make_request_with_retry(
                'POST', 
                self.LOGIN_URL, 
                headers=headers, 
                json=payload
            )
            
            data = response.json()
            self.auth_token = data.get('auth_token')
            
            if not self.auth_token:
                logger.error("No auth token received from Hevy API")
                return False
                
            logger.info("Successfully authenticated with Hevy API")
            return True
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Hevy login failed: {e}")
            return False
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Hevy login response: {e}")
            return False
    
    def get_workouts(self, limit: int = 50) -> Optional[List[Dict[str, Any]]]:
        """
        Fetch workout data from Hevy API.
        
        Args:
            limit: Maximum number of workouts to retrieve
            
        Returns:
            List of workout data or None if failed
        """
        if not self.auth_token:
            logger.error("Not authenticated with Hevy API")
            return None
            
        try:
            headers = {
                'accept': 'application/json, text/plain, */*',
                'x-api-key': self.WORKOUTS_API_KEY,
                'auth-token': self.auth_token,
                'Host': 'api.hevyapp.com',
                'User-Agent': 'okhttp/4.9.3',
                'Pragma': 'no-cache',
                'Cache-Control': 'no-cache',
            }
            
            response = self._make_request_with_retry(
                'GET', 
                self.WORKOUTS_URL, 
                headers=headers
            )
            
            data = response.json()
            workouts = data.get('workouts', [])
            
            # Limit results if specified
            if limit and len(workouts) > limit:
                workouts = workouts[:limit]
                
            logger.info(f"Retrieved {len(workouts)} workouts from Hevy")
            return workouts
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to fetch Hevy workouts: {e}")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Hevy workouts response: {e}")
            return None
    
    def get_workouts_by_date_range(self, start_date: datetime, end_date: datetime) -> Optional[List[Dict[str, Any]]]:
        """
        Fetch workouts within a specific date range.
        
        Args:
            start_date: Start date for workout retrieval
            end_date: End date for workout retrieval
            
        Returns:
            List of workouts within date range or None if failed
        """
        workouts = self.get_workouts()
        if not workouts:
            return None
            
        filtered_workouts = []
        start_timestamp = int(start_date.timestamp())
        end_timestamp = int(end_date.timestamp())
        
        for workout in workouts:
            workout_time = workout.get('start_time', 0)
            if start_timestamp <= workout_time <= end_timestamp:
                filtered_workouts.append(workout)
                
        logger.info(f"Filtered to {len(filtered_workouts)} workouts in date range")
        return filtered_workouts
    
    def export_workouts_to_csv_format(self, workouts: List[Dict[str, Any]]) -> Dict[str, List[str]]:
        """
        Convert workout data to CSV-like format for integration with existing pipeline.
        
        Args:
            workouts: List of workout data from API
            
        Returns:
            Dictionary with CSV-formatted workout data
        """
        if not workouts:
            return {}
            
        # Create CSV-like data for workouts
        workout_data = []
        exercise_data = []
        
        for workout in workouts:
            # Workout summary
            workout_date = datetime.fromtimestamp(workout.get('start_time', 0)).strftime('%Y-%m-%d')
            workout_row = {
                'id': workout.get('id', ''),
                'date': workout_date,
                'name': workout.get('name', ''),
                'start_time': workout.get('start_time', ''),
                'end_time': workout.get('end_time', ''),
                'duration_seconds': workout.get('end_time', 0) - workout.get('start_time', 0),
                'estimated_volume_kg': workout.get('estimated_volume_kg', 0),
                'exercise_count': len(workout.get('exercises', [])),
            }
            workout_data.append(workout_row)
            
            # Exercise details
            for exercise in workout.get('exercises', []):
                for set_data in exercise.get('sets', []):
                    exercise_row = {
                        'workout_id': workout.get('id', ''),
                        'workout_date': workout_date,
                        'workout_name': workout.get('name', ''),
                        'exercise_title': exercise.get('title', ''),
                        'exercise_template_id': exercise.get('exercise_template_id', ''),
                        'muscle_group': exercise.get('muscle_group', ''),
                        'equipment_category': exercise.get('equipment_category', ''),
                        'set_index': set_data.get('index', 0),
                        'weight_kg': set_data.get('weight_kg', 0),
                        'reps': set_data.get('reps', 0),
                        'duration_seconds': set_data.get('duration_seconds', 0),
                        'distance_meters': set_data.get('distance_meters', 0),
                        'rpe': set_data.get('rpe', ''),
                    }
                    exercise_data.append(exercise_row)
        
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
            # Navigate to settings page and look for export data section
            logger.info("Navigating to settings page for export options")
            
            settings_urls = [
                "https://api.hevyapp.com/settings",
                "https://hevy.com/settings",
                "https://hevy.com/account"
            ]
            
            settings_loaded = False
            for url in settings_urls:
                try:
                    response = self._make_request_with_retry('GET', url)
                    if response.status_code == 200:
                        settings_loaded = True
                        logger.info(f"Successfully loaded settings from {url}")
                        break
                except Exception as e:
                    logger.warning(f"Failed to load settings from {url}: {e}")
                    continue
            
            if not settings_loaded:
                logger.error("Could not load settings page")
                return {'error': 'Settings page not accessible'}
            
            # Look for export data section in settings
            logger.info("Looking for export data section in settings")
            
            # Try to navigate to export section directly
            export_section_selectors = [
                'button:has-text("Export Data")',
                'a:has-text("Export Data")',
                '[data-testid*="export"]',
                '[class*="export"]',
                'button:has-text("Export")',
                'a:has-text("Export")'
            ]
            
            export_found = False
            for selector in export_section_selectors:
                try:
                    export_button = self.page.locator(selector).first
                    if export_button.is_visible(timeout=2000):
                        logger.info(f"Found export section: {selector}")
                        export_button.click()
                        self.page.wait_for_timeout(3000)
                        export_found = True
                        break
                except Exception as e:
                    logger.warning(f"Export section selector {selector} failed: {e}")
                    continue
            
            if not export_found:
                logger.warning("Could not find export data section, proceeding with workout scraping")
            
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


def create_hevy_client() -> HevyRPCClient:
    """Create and return a Hevy RPC client instance."""
    return HevyRPCClient()


# Test function
def test_hevy_client():
    """Test the Hevy RPC client with environment variables."""
    import os
    from dotenv import load_dotenv
    
    load_dotenv()
    
    username = os.getenv('HEVY_USERNAME')
    password = os.getenv('HEVY_PASSWORD')
    
    if not username or not password:
        print("HEVY_USERNAME and HEVY_PASSWORD environment variables required")
        return
    
    client = create_hevy_client()
    
    # Test login
    print("Testing Hevy login...")
    if client.login(username, password):
        print("✓ Login successful")
        
        # Test workout retrieval
        print("Testing workout retrieval...")
        workouts = client.get_workouts(limit=5)
        if workouts:
            print(f"✓ Retrieved {len(workouts)} workouts")
            
            # Test export
            print("Testing data export...")
            files = client.export_all_to_files()
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
    test_hevy_client()
