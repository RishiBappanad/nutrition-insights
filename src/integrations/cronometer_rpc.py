"""
Cronometer RPC Client Integration

This module provides direct API access to Cronometer using RPC calls,
eliminating the need for browser automation.
"""

import csv
import re
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any
from urllib.parse import urljoin

import requests

logger = logging.getLogger(__name__)

# Cronometer API endpoints
HTML_LOGIN_URL = "https://cronometer.com/login/"
API_LOGIN_URL = "https://cronometer.com/login"
GWT_BASE_URL = "https://cronometer.com/cronometer/app"
API_EXPORT_URL = "https://cronometer.com/export"

# GWT RPC constants
GWT_CONTENT_TYPE = "text/x-gwt-rpc; charset=UTF-8"
GWT_MODULE_BASE = "https://cronometer.com/cronometer/"
GWT_PERMUTATION = "7B121DC5483BF272B1BC1916DA9FA963"
GWT_HEADER = "2D6A926E3729946302DC68073CB0D550"

GWT_AUTHENTICATE = (
    "7|0|5|https://cronometer.com/cronometer/|"
    + GWT_HEADER
    + "|com.cronometer.shared.rpc.CronometerService|authenticate|java.lang.Integer/3438268394|1|2|3|4|1|5|5|-300|"
)
GWT_GENERATE_AUTH_TOKEN = (
    "7|0|8|https://cronometer.com/cronometer/|"
    + GWT_HEADER
    + "|com.cronometer.shared.rpc.CronometerService|generateAuthorizationToken"
    "|java.lang.String/2004016611|I|com.cronometer.shared.user.AuthScope/2065601159|%s|1|2|3|4|4|5|6|6|7|8|%s|3600|7|2|"
)

# Regex patterns for parsing responses
CSRF_RE = re.compile(r'name="anticsrf"\s+value="([^"]+)"')
GWT_AUTH_RE = re.compile(r"OK\[(\d*),")
TOKEN_RE = re.compile(r'"([^"]+)"')


class CronometerRPCClient:
    """
    Direct RPC client for Cronometer API.
    
    This client handles authentication and data export using Cronometer's
    internal GWT RPC endpoints, providing faster and more reliable access
    than browser automation.
    """
    
    def __init__(self, username: Optional[str] = None, password: Optional[str] = None):
        """
        Initialize the RPC client.
        
        Args:
            username: Cronometer username (defaults to CRONOMETER_USERNAME env var)
            password: Cronometer password (defaults to CRONOMETER_PASSWORD env var)
        """
        from dotenv import load_dotenv
        load_dotenv()
        
        self.username = username or os.getenv("CRONOMETER_USERNAME")
        self.password = password or os.getenv("CRONOMETER_PASSWORD")
        self.session = requests.Session()
        self.nonce = None
        self.user_id = None
        
        if not self.username or not self.password:
            raise ValueError("Cronometer credentials not provided via arguments or environment variables")

    def _get_csrf_token(self) -> str:
        """Extract CSRF token from login page."""
        resp = self.session.get(HTML_LOGIN_URL, timeout=30)
        resp.raise_for_status()
        match = CSRF_RE.search(resp.text)
        if not match:
            raise ValueError("unable to find anticsrf token in login page")
        return match.group(1)

    def login(self) -> None:
        """Authenticate with Cronometer using username/password."""
        try:
            logger.info("Authenticating with Cronometer...")
            
            # Get CSRF token and login
            csrf = self._get_csrf_token()
            data = {
                "anticsrf": csrf,
                "username": self.username,
                "password": self.password,
            }
            resp = self.session.post(API_LOGIN_URL, data=data, timeout=30)
            resp.raise_for_status()
            
            # Check for login errors
            body = resp.text
            if "error" in body.lower():
                try:
                    payload = resp.json()
                    if payload.get("error"):
                        raise ValueError(f"login failed: {payload['error']}")
                except ValueError:
                    pass

            # Update nonce and authenticate via GWT
            self._update_nonce_from_cookies()
            self._gwt_authenticate()
            
            logger.info("Successfully authenticated with Cronometer")
            
        except Exception as e:
            logger.error(f"Cronometer authentication failed: {e}")
            raise

    def _update_nonce_from_cookies(self) -> None:
        """Extract nonce from session cookies."""
        for cookie in self.session.cookies:
            if cookie.name == "sesnonce":
                self.nonce = cookie.value
                return
        raise ValueError("sesnonce cookie not found after login")

    def _gwt_authenticate(self) -> None:
        """Perform GWT RPC authentication."""
        headers = {
            "Content-Type": GWT_CONTENT_TYPE,
            "x-gwt-module-base": GWT_MODULE_BASE,
            "x-gwt-permutation": GWT_PERMUTATION,
        }
        resp = self.session.post(GWT_BASE_URL, headers=headers, data=GWT_AUTHENTICATE, timeout=30)
        resp.raise_for_status()
        self._update_nonce_from_cookies()
        match = GWT_AUTH_RE.search(resp.text)
        if not match:
            raise ValueError("failed to parse GWT auth response")
        self.user_id = match.group(1)

    def _generate_auth_token(self) -> str:
        """Generate authorization token for API calls."""
        if not self.nonce or not self.user_id:
            raise ValueError("client must be logged in before generating auth token")

        headers = {
            "Content-Type": GWT_CONTENT_TYPE,
            "x-gwt-module-base": GWT_MODULE_BASE,
            "x-gwt-permutation": GWT_PERMUTATION,
        }
        body = GWT_GENERATE_AUTH_TOKEN % (self.nonce, self.user_id)
        resp = self.session.post(GWT_BASE_URL, headers=headers, data=body, timeout=30)
        resp.raise_for_status()
        match = TOKEN_RE.search(resp.text)
        if not match:
            raise ValueError("failed to parse token from GWT response")
        return match.group(1)

    def _new_export_headers(self) -> Dict[str, str]:
        """Generate headers for export requests."""
        return {
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "same-origin",
        }

    def export(self, export_type: str, start_date: str, end_date: str) -> str:
        """
        Export data from Cronometer.
        
        Args:
            export_type: Type of export ('servings', 'dailySummary', 'exercises', 'biometrics', 'notes')
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
            
        Returns:
            CSV data as string
        """
        token = self._generate_auth_token()
        params = {
            "nonce": token,
            "generate": export_type,
            "start": start_date,
            "end": end_date,
        }
        resp = self.session.get(API_EXPORT_URL, headers=self._new_export_headers(), params=params, timeout=60)
        resp.raise_for_status()
        return resp.text

    def export_servings(self, start_date: str, end_date: str) -> str:
        """Export food servings data."""
        return self.export("servings", start_date, end_date)

    def export_daily_nutrition(self, start_date: str, end_date: str) -> str:
        """Export daily nutrition summary."""
        return self.export("dailySummary", start_date, end_date)

    def export_exercises(self, start_date: str, end_date: str) -> str:
        """Export exercises data."""
        return self.export("exercises", start_date, end_date)

    def export_biometrics(self, start_date: str, end_date: str) -> str:
        """Export biometrics data."""
        return self.export("biometrics", start_date, end_date)

    def export_notes(self, start_date: str, end_date: str) -> str:
        """Export notes data."""
        return self.export("notes", start_date, end_date)

    def export_all_to_files(self, start_date: str, end_date: str, output_dir: str = "raw_data") -> Dict[str, Optional[str]]:
        """
        Export all data types to files.
        
        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
            output_dir: Directory to save CSV files
            
        Returns:
            Dictionary mapping export types to file paths
        """
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        results = {}
        
        export_methods = [
            ("daily_summary", self.export_daily_nutrition),
            ("servings", self.export_servings),
            ("exercises", self.export_exercises),
            ("biometrics", self.export_biometrics),
            ("notes", self.export_notes),
        ]
        
        for export_type, method in export_methods:
            try:
                logger.info(f"Exporting {export_type} data...")
                csv_data = method(start_date, end_date)
                
                filename = f"cronometer_{export_type}_{timestamp}.csv"
                filepath = output_path / filename
                
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(csv_data)
                
                results[export_type] = str(filepath)
                logger.info(f"Saved {export_type} data to {filepath}")
                
            except Exception as e:
                logger.error(f"Failed to export {export_type}: {e}")
                results[export_type] = None
        
        return results


def parse_servings_csv(raw_csv: str) -> list[Dict[str, Any]]:
    """Parse servings CSV data into list of dictionaries."""
    reader = csv.DictReader(raw_csv.splitlines())
    rows = []
    for row in reader:
        parsed = {k: _try_parse_number(v) for k, v in row.items()}
        rows.append(parsed)
    return rows


def _try_parse_number(value: str) -> Any:
    """Attempt to parse a string as int, then float, otherwise return as string."""
    if value is None:
        return None
    value = value.strip()
    if value == "":
        return None
    try:
        return int(value)
    except ValueError:
        try:
            return float(value)
        except ValueError:
            return value


# Import os for environment variables
import os
