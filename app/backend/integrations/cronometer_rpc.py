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
# Modern REST v3 API — used for food/recipe search only (confirmed via a
# real captured request: GET /api/v3/user/{userId}/food-search/string).
# This coexists with the legacy GWT RPC service below; Cronometer hasn't
# migrated diary/recipe read+write off GWT RPC as of this capture.
API_V3_BASE = "https://cronometer.com/api/v3"

# GWT RPC constants.
#
# GWT_PERMUTATION is captured, not derived — GWT compiles a distinct
# permutation hash per supported browser/user-agent, and Cronometer
# appears to recompile/redeploy periodically (the value hardcoded here
# previously, 0AC0B7E4D7F952D1D90194EA6F2AC472, returned a 404 when
# checked live — confirmed stale). This value was captured from a real
# authenticated browser session's Network tab on 2026-07-20. If calls
# start failing, the fix is re-capturing a fresh permutation from a live
# session's request headers (x-gwt-permutation), not guessing.
GWT_PERMUTATION = "8119D24F8CC7814B83B62DD87A7C62D8"
GWT_HEADER = "F25561B47C31168F0ED80B768B647985"
GWT_CONTENT_TYPE = "text/x-gwt-rpc; charset=UTF-8"
GWT_MODULE_BASE = "https://cronometer.com/cronometer/"

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

# Meal-type encoding for updateDiary/AddEntryChange, reverse-engineered
# from 5 real captured requests (varying meal while holding food+quantity
# constant): the packed field is (mealType << 16) | localEntryId, where
# localEntryId is a per-day sequence number the server assigns/ignores on
# a new entry (safe to send as 0). Confirmed via captured payloads:
#   Breakfast qty1 -> 65539  (1<<16 | 3)
#   Breakfast qty2 -> 65540  (1<<16 | 4)
#   Dinner    qty1 -> 196609 (3<<16 | 1)
#   Lunch     qty3 -> 131074 (2<<16 | 2)  [donuts]
#   Snack     qty1 -> 262155 (4<<16 | 11)
MEAL_TYPE_CODES = {"breakfast": 1, "lunch": 2, "dinner": 3, "snack": 4}

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

    def set_bmr(self, value: int) -> bool:
        """Set a custom BMR value in Cronometer.

        Args:
            value: BMR in kcal (e.g. 1700)

        Returns:
            True on success
        """
        headers = {
            "Content-Type": GWT_CONTENT_TYPE,
            "x-gwt-module-base": GWT_MODULE_BASE,
            "x-gwt-permutation": GWT_PERMUTATION,
        }
        payload = (
            "7|0|9|https://cronometer.com/cronometer/|"
            f"{GWT_HEADER}|"
            "com.cronometer.shared.rpc.CronometerService|"
            "setUserPreference|"
            "java.lang.String/2004016611|"
            "I|"
            f"{self.nonce}|"
            "bmr|"
            f"{value}|"
            f"1|2|3|4|4|5|6|5|5|7|{self.user_id}|8|9|"
        )
        resp = self.session.post(GWT_BASE_URL, headers=headers, data=payload, timeout=30)
        resp.raise_for_status()
        success = resp.text.startswith("//OK")
        if success:
            logger.info(f"BMR set to {value} kcal")
        else:
            logger.error(f"Failed to set BMR: {resp.text}")
        return success

    def search_food(self, query: str, max_results: int = 50) -> list[Dict[str, Any]]:
        """
        Search Cronometer's food database via the modern REST v3 API
        (confirmed working via a real captured request — a different,
        non-GWT endpoint from everything else in this client).

        Results include both plain foods and the user's own recipes/meals
        (distinguished by the `recipe`/`meal`/`recipeOrMeal` boolean
        fields on each result) — Cronometer unifies foods, recipes, and
        meals under one searchable ID space, confirmed by a captured
        updateDiary call that referenced a recipe by the same kind of
        numeric ID as a plain food.

        Returns the raw list of result dicts (fields include: name, id,
        source, measureId, measureDisplayName, recipe, meal, recipeOrMeal)
        — deliberately not reshaped here, since this is a thin pass-through
        to a real external API and callers may need fields not yet used
        by any caller in this codebase.
        """
        if not self.user_id:
            raise ValueError("client must be logged in before searching food")
        resp = self.session.get(
            f"{API_V3_BASE}/user/{self.user_id}/food-search/string",
            params={
                "query": query,
                "maxResults": max_results,
                "sources": "All",
                "categoryId": 0,
                "selectedTab": "ALL",
                "type": "All",
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def list_my_foods(self) -> str:
        """
        List the user's own custom foods, recipes, and meals via the
        findMyFoods GWT RPC method (confirmed via a real captured
        request/response). Returns the raw GWT-RPC response text — the
        response encoding for this call hasn't been fully decoded yet
        (unlike updateDiary's request encoding, which was reverse-
        engineered from multiple varied captures); callers needing
        structured data should decode further once a real response
        payload has been captured and verified, not guessed.
        """
        if not self.nonce or not self.user_id:
            raise ValueError("client must be logged in before listing foods")
        headers = {
            "Content-Type": GWT_CONTENT_TYPE,
            "x-gwt-module-base": GWT_MODULE_BASE,
            "x-gwt-permutation": GWT_PERMUTATION,
        }
        payload = (
            "7|0|7|https://cronometer.com/cronometer/|"
            f"{GWT_HEADER}|com.cronometer.shared.rpc.CronometerService|findMyFoods|"
            f"java.lang.String/2004016611|I|{self.nonce}|1|2|3|4|2|5|6|7|{self.user_id}|"
        )
        resp = self.session.post(GWT_BASE_URL, headers=headers, data=payload, timeout=30)
        resp.raise_for_status()
        return resp.text

    def log_diary_entry(self, food_id: int, measure_id: int, meal: str, quantity: float,
                         day: Optional[str] = None) -> bool:
        """
        Log one entry (a plain food OR a recipe — same call, same
        encoding, confirmed via a real captured request for each) to the
        diary via updateDiary/AddEntryChange.

        Args:
            food_id: Cronometer's own numeric ID for the food or recipe
                (from search_food's `id` field — this is NOT a TrackStack
                food_log id or a USDA fdcId; Cronometer has its own food
                database and ID space, confirmed distinct from USDA's).
            measure_id: Cronometer's numeric ID for the serving/measure
                to log against (from search_food's `measureId` field —
                each food has one or more measures, e.g. "1 donut" vs
                "100g"; this determines what `quantity` scales).
            meal: one of "breakfast", "lunch", "dinner", "snack"
                (case-insensitive) — see MEAL_TYPE_CODES, reverse-
                engineered from 5 real captures that isolated meal type
                from quantity.
            quantity: how many of `measure_id`'s serving to log (e.g. 3
                for "3 donuts" if measure_id is the "1 donut" measure).
                Reverse-engineered as quantity*100 in the wire format
                (confirmed via 3 captures varying only quantity: qty 1/2/3
                -> wire values 100/200/300).
            day: "YYYY-MM-DD" date to log to, defaults to today.

        Returns:
            True on success.

        This is a REAL WRITE to the user's live Cronometer diary — no
        dry-run mode exists at this layer. Callers (the sync router) are
        responsible for any confirmation UX before invoking this.
        """
        if not self.nonce or not self.user_id:
            raise ValueError("client must be logged in before logging a diary entry")
        meal_code = MEAL_TYPE_CODES.get(meal.lower())
        if meal_code is None:
            raise ValueError(f"meal must be one of {sorted(MEAL_TYPE_CODES)}, got {meal!r}")
        if quantity <= 0:
            raise ValueError("quantity must be positive")

        if day is None:
            day = datetime.now().strftime("%Y-%m-%d")
        year, month, dom = (int(p) for p in day.split("-"))

        meal_packed = meal_code << 16  # local entry id 0 — server assigns/ignores on new entries
        qty_wire = round(quantity * 100)

        headers = {
            "Content-Type": GWT_CONTENT_TYPE,
            "x-gwt-module-base": GWT_MODULE_BASE,
            "x-gwt-permutation": GWT_PERMUTATION,
        }
        payload = (
            "7|0|12|https://cronometer.com/cronometer/|"
            f"{GWT_HEADER}|com.cronometer.shared.rpc.CronometerService|updateDiary|"
            "java.lang.String/2004016611|I|java.util.List|"
            f"{self.nonce}|"
            "java.util.Collections$SingletonList/1586180994|"
            "com.cronometer.shared.entries.changes.AddEntryChange/3949104564|"
            "com.cronometer.shared.entries.models.Serving/2553599101|"
            "com.cronometer.shared.entries.models.Day/782579793|"
            f"1|2|3|4|3|5|6|7|8|{self.user_id}|9|10|1|1|11|12|{dom}|{month}|{year}|"
            f"1|1|0|{meal_packed}|0|0|{qty_wire}|{food_id}|A|{measure_id}|0|0|"
        )
        resp = self.session.post(GWT_BASE_URL, headers=headers, data=payload, timeout=30)
        resp.raise_for_status()
        success = resp.text.startswith("//OK")
        if success:
            logger.info(f"Logged food/recipe {food_id} (measure {measure_id}) x{quantity} to {meal} on {day}")
        else:
            logger.error(f"Failed to log diary entry: {resp.text}")
        return success

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
        Export all data types to files with fixed filenames (overwritten each run).
        
        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
            output_dir: Directory to save CSV files
            
        Returns:
            Dictionary mapping export types to file paths
        """
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
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
                
                filename = f"cronometer_{export_type}.csv"
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
