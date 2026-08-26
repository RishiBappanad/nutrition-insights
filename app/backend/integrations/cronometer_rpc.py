"""
Cronometer RPC Client Integration

This module provides direct API access to Cronometer using RPC calls,
eliminating the need for browser automation.
"""

import csv
import re
import logging
import uuid
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
GWT_CONTENT_TYPE = "text/x-gwt-rpc; charset=UTF-8"
GWT_MODULE_BASE = "https://cronometer.com/cronometer/"

# GWT_HEADER used to be a SEPARATE stale constant from GWT_PERMUTATION --
# a real, independent bug found while verifying add_exercise() against
# fresh captures: GWT_PERMUTATION (used only in the x-gwt-permutation
# HTTP header) had already been corrected to the live value
# (8119D24F8CC7814B83B62DD87A7C62D8), but GWT_HEADER (embedded inline in
# several payload BODIES: authenticate, generateAuthorizationToken,
# search_food, findMyFoods, log_diary_entry) was still the OLD value
# (F25561B47C31168F0ED80B768B647985) -- confirmed stale by comparing
# against real 2026-07-21 captures, which show the current permutation
# hash inline in the payload body too, not just the header. The two were
# never meant to diverge (GWT sends the same permutation value in both
# places for the same client build) -- this was a partial fix that only
# updated one of the two occurrences. Fixed by making GWT_HEADER an
# alias for GWT_PERMUTATION so there's one source of truth going
# forward; whichever needs re-capturing next time, both update together.
GWT_HEADER = GWT_PERMUTATION

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
        """Generate headers for export requests - mimic a real browser."""
        return {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Referer": "https://cronometer.com/#export",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
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
        request/response). Returns the raw GWT-RPC response text.
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

    def parse_find_my_foods(self, raw_gwt: str) -> list[Dict[str, Any]]:
        """
        Parse raw GWT-RPC response from findMyFoods into structured list of
        {'food_id': int, 'name': str}.
        """
        body = raw_gwt[len("//OK["):-1] if raw_gwt.startswith("//OK[") and raw_gwt.endswith("]") else raw_gwt
        if body.startswith("//OK"):
            body = body[4:]
        
        table_start = body.rfind('[')
        if table_start == -1:
            return []
        
        # String table at end
        str_table_raw = body[table_start:]
        import json
        try:
            strings = json.loads(str_table_raw)
        except Exception:
            strings = []

        # Find all large integers (food_ids, typically > 10000)
        numeric_part = body[:table_start]
        tokens = [t.strip() for t in numeric_part.split(',') if t.strip()]
        
        food_ids = []
        for t in tokens:
            try:
                val = int(t)
                if val > 100000:
                    food_ids.append(val)
            except ValueError:
                pass

        results = []
        # Pair food_ids with string names in reverse order of seq_idx
        for idx, fid in enumerate(food_ids):
            if idx < len(strings):
                name = strings[idx]
                if name and isinstance(name, str):
                    results.append({"food_id": fid, "name": name})
        return results

    def get_food(self, food_id: int) -> Dict[str, Any]:
        """
        Fetch details for a specific food or recipe from Cronometer by food_id via getFood.
        Returns dict with: name, is_recipe, ingredients, nutrients
        """
        if not self.user_id:
            raise ValueError("client must be logged in before getting food")
        headers = {
            "Content-Type": GWT_CONTENT_TYPE,
            "x-gwt-module-base": GWT_MODULE_BASE,
            "x-gwt-permutation": GWT_PERMUTATION,
        }
        payload = (
            "7|0|5|https://cronometer.com/cronometer/|"
            f"{GWT_HEADER}|com.cronometer.shared.rpc.CronometerService|getFood|"
            f"I|{food_id}|1|2|3|4|1|5|"
        )
        resp = self.session.post(GWT_BASE_URL, headers=headers, data=payload, timeout=30)
        resp.raise_for_status()
        raw = resp.text

        # Extract name from string table
        body = raw[len("//OK["):-1] if raw.startswith("//OK[") and raw.endswith("]") else raw
        if body.startswith("//OK"):
            body = body[4:]
        
        table_start = body.rfind('[')
        name = f"Food #{food_id}"
        if table_start != -1:
            try:
                import json
                strings = json.loads(body[table_start:])
                if strings and isinstance(strings[0], str):
                    name = strings[0]
            except Exception:
                pass

        # Parse numeric tokens for ingredients and nutrients
        numeric_part = body[:table_start] if table_start != -1 else body
        tokens = [t.strip() for t in numeric_part.split(',') if t.strip()]
        
        # Check if recipe (contains ingredient markers)
        ingredients = []
        is_recipe = False
        
        # Scan for ingredients: [tag, 0, id, code, target_food_id, amount_grams, 4]
        for i in range(len(tokens) - 6):
            try:
                tag = int(tokens[i+6])
                if tag == 4:  # Ingredient tag
                    target_id = int(tokens[i+4])
                    amount = float(tokens[i+5])
                    if target_id > 1000:
                        ingredients.append({"food_id": target_id, "amount_grams": amount})
                        is_recipe = True
            except (ValueError, IndexError):
                pass

        # Extract nutrients (GWT nutrient IDs: 208=calories, 203=protein, 204=fat, 205=carbs, 291=fiber)
        NUTRIENT_ID_MAP = {208: "calories", 203: "protein", 204: "fat", 205: "carbs", 291: "fiber"}
        nutrients = {}
        for i in range(len(tokens) - 2):
            try:
                nid = int(tokens[i])
                if nid in NUTRIENT_ID_MAP:
                    val = float(tokens[i+1])
                    nutrients[NUTRIENT_ID_MAP[nid]] = abs(val)
            except (ValueError, IndexError):
                pass

        return {
            "food_id": food_id,
            "name": name,
            "is_recipe": is_recipe,
            "ingredients": ingredients,
            "nutrients": nutrients,
        }

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

    # addExercise wire format -- CONFIRMED via 3 controlled write captures:
    # 2 varying ONLY duration/calories for a real catalog activity
    # ("Martial Arts, Moderate", activityId 1157), and a 3rd logging a
    # genuinely novel, never-before-logged CUSTOM activity ("Custom
    # Exercise", no catalog match) -- CROSS-VALIDATED against 14 real
    # historical records returned by getRecentExercises (same
    # com.cronometer.shared.exercise.Exercise class, response-side).
    # This meets the same bar log_diary_entry's fields were confirmed to
    # (isolate one variable at a time across real captures) -- unlike
    # addFood/editFood, which remain deliberately unimplemented because
    # no amount of captures gathered so far isolated their nested
    # Food->List<Ingredient> fields this cleanly.
    #
    # The custom-activity capture was the key unlock: comparing it
    # against the catalog-activity captures showed activity_id AND the
    # intensity-code field both drop to 0 for a custom entry, while
    # met_coefficient stayed IDENTICAL (140.99988486671424) across ALL
    # THREE captures -- proving met_coefficient is NOT tied to the
    # specific activity at all, it's a per-USER constant (almost
    # certainly derived from the logged-in user's own body weight/
    # profile on Cronometer's side, which didn't change between
    # captures). This means every exercise can be logged as a "custom"
    # entry (activity_id=0, intensity=0) using a caller-supplied
    # activity_name string and a caller-supplied calories_burned value
    # Cronometer will accept and store as-is -- no activity catalog
    # lookup needed at all, which sidesteps the exact problem that made
    # this look intractable at first (no findActivities-derived
    # activity_id/met_coefficient resolution required for the common
    # case of a TrackStack-native manual log being pushed to Cronometer).
    #
    # Field-by-position (0-indexed within the trailing value block):
    #   0:  activity_id -- 0 for a custom/no-catalog-match activity
    #       (confirmed via the "Custom Exercise" capture); a real
    #       catalog id (e.g. 1157, 1138, 1139, 1290 -- all confirmed via
    #       getRecentExercises/findActivities) for a matched one.
    #   1:  intensity/type code -- 0 for custom (confirmed); varies per
    #       catalog activity+intensity combo otherwise (was 50 for
    #       "Martial Arts, Moderate" -- NOT assumed to generalize to
    #       other catalog activities, since this client only ever
    #       exercises the activity_id=0 custom path).
    #   2:  constant 0
    #   3:  calories_burned, NEGATED (exercise burn is a negative delta)
    #   4:  constant 9 (unconfirmed what it represents; identical across
    #       all 3 write captures)
    #   5:  day-of-month
    #   6:  month
    #   7:  year
    #   8:  shortId (a short opaque per-entry string Cronometer
    #       generates client-side, e.g. "A" -- NOT confirmed whether the
    #       server requires a specific format/uniqueness; using a fixed
    #       short random-looking token per call, matching the real
    #       captures' shape)
    #   9,10: constant 0, 0
    #   11: duration_minutes
    #   12: constant 10 (write-side type/action discriminator --
    #       differs from the response-side value 4 for the same field
    #       position, consistent with a "this is a NEW entry" marker
    #       rather than user data)
    #   13: constant 0
    #   14: constant 1 (write-side discriminator, differs from
    #       response-side 2 -- same reasoning as position 12)
    #   15,16: constant 0, 0
    #   17: userId (confirmed identical to getRecentExercises' userId
    #       field, and identical to self.user_id used elsewhere)
    #   18: metCoef -- a per-USER floating constant (see above; NOT
    #       per-activity, confirmed via the custom-activity capture).
    #       Defaults to the one real captured value below; re-capture if
    #       calls start failing for a different account, same caveat as
    #       GWT_PERMUTATION.
    #   19: repeats the userId value again (confirmed identical to
    #       position 17 in every capture -- kept as a literal repeat,
    #       not derived, since nothing demonstrates it's ever different)
    #
    # DEFAULT_MET_COEFFICIENT is captured from ONE real account -- it is
    # NOT verified to be the same for every Cronometer user (it plausibly
    # varies with the account's own weight/profile, per the reasoning
    # above). Callers logging to a DIFFERENT account than the one this
    # was captured from should supply their own recent value (e.g. read
    # back from that account's own getRecentExercises) rather than trust
    # this default blindly.
    DEFAULT_MET_COEFFICIENT = 140.99988486671424

    def add_exercise(
        self,
        activity_name: str,
        duration_minutes: float,
        calories_burned: float,
        activity_id: int = 0,
        met_coefficient: Optional[float] = None,
        day: Optional[str] = None,
    ) -> bool:
        """
        Log one exercise/activity entry to the diary via addExercise.
        Confirmed working format (see the field-by-field breakdown
        above) -- a REAL WRITE to the user's live Cronometer account,
        no dry-run mode at this layer, same caveat as log_diary_entry.

        Defaults to logging as a CUSTOM activity (activity_id=0), which
        is fully general -- works for any activity_name, no catalog
        lookup required, and is the confirmed-safe path (verified via a
        real capture of a genuinely novel custom activity). Pass a real
        `activity_id` (e.g. from a prior getRecentExercises/
        findActivities lookup for THIS account) if you want the entry
        to associate with Cronometer's own activity catalog instead —
        untested for activity ids that weren't part of this account's
        own history, so treat that path as lower-confidence than the
        default custom path.

        Args:
            activity_name: display name shown in Cronometer's diary
                (e.g. "Running", "Custom Exercise") -- sent as a real
                wire field for the custom path, confirmed via capture.
            duration_minutes: length of the activity in minutes.
            calories_burned: calories burned, as a positive number (this
                method negates it internally to match the wire format).
            activity_id: Cronometer's numeric catalog id, or 0 (default)
                for a custom/no-catalog-match entry.
            met_coefficient: per-user floating constant (see
                DEFAULT_MET_COEFFICIENT above) — defaults to the one
                real captured value if not supplied.
            day: "YYYY-MM-DD", defaults to today.

        Returns:
            True on success.
        """
        if not self.nonce or not self.user_id:
            raise ValueError("client must be logged in before logging an exercise entry")
        if duration_minutes <= 0:
            raise ValueError("duration_minutes must be positive")
        if calories_burned < 0:
            raise ValueError("calories_burned must not be negative (this method negates it internally)")

        if met_coefficient is None:
            met_coefficient = self.DEFAULT_MET_COEFFICIENT
        if day is None:
            day = datetime.now().strftime("%Y-%m-%d")
        year, month, dom = (int(p) for p in day.split("-"))
        short_id = uuid.uuid4().hex[:5]
        intensity_code = 0 if activity_id == 0 else 50

        headers = {
            "Content-Type": GWT_CONTENT_TYPE,
            "x-gwt-module-base": GWT_MODULE_BASE,
            "x-gwt-permutation": GWT_PERMUTATION,
        }
        payload = (
            "7|0|10|https://cronometer.com/cronometer/|"
            f"{GWT_HEADER}|com.cronometer.shared.rpc.CronometerService|addExercise|"
            "java.lang.String/2004016611|com.cronometer.shared.exercise.Exercise/2894167537|I|"
            f"{self.nonce}|"
            "com.cronometer.shared.entries.models.Day/782579793|"
            f"{activity_name}|1|2|3|4|3|5|6|7|8|"
            f"{activity_id}|{intensity_code}|0|{-abs(calories_burned)}|9|{dom}|{month}|{year}|{short_id}|0|0|"
            f"{duration_minutes}|10|0|1|0|0|{self.user_id}|{met_coefficient}|{self.user_id}|"
        )
        resp = self.session.post(GWT_BASE_URL, headers=headers, data=payload, timeout=30)
        resp.raise_for_status()
        success = resp.text.startswith("//OK")
        if success:
            logger.info(f"Logged exercise {activity_name!r} (id {activity_id}) x{duration_minutes}min on {day}")
        else:
            logger.error(f"Failed to log exercise entry: {resp.text}")
        return success

    # getRecentExercises response format -- CONFIRMED via one real
    # captured response containing 14 real historical exercise records.
    # The response is GWT-RPC's array-of-objects encoding: a flat value
    # array (20 values per record: activityId at position 4 within each
    # 20-wide chunk, anchored on this account's userId appearing at a
    # FIXED position 1 within each chunk -- confirmed by locating every
    # occurrence of the userId value and checking the gaps between them
    # were all exactly 20), followed by a trailing GWT string table and
    # a couple of small metadata ints. The per-record field layout below
    # is the SAME Exercise class as add_exercise()'s write path, so this
    # reuses that confirmed mapping rather than a second independent
    # guess. See add_exercise()'s field-by-field comment for the fields
    # NOT decoded here either (this parser only extracts what's needed
    # to re-log an entry via add_exercise() -- met_coefficient,
    # activity_id, duration, calories, and the date -- not every field).
    def get_recent_exercises(self) -> list[Dict[str, Any]]:
        """
        Fetch the user's recent exercise history via getRecentExercises.
        Returns a list of dicts: {activity_id, met_coefficient,
        duration_minutes, calories_burned (positive), date
        ("YYYY-MM-DD")} -- one per historical entry.

        KNOWN GAP, not silently papered over: activity NAME is not
        reliably resolvable from this response. The trailing GWT string
        table in a real captured 14-record response contained only ONE
        actual activity-name string ("Martial Arts, Moderate") despite
        14 distinct records with different activity_ids (1146, 1341,
        1231, 1176, 1160, 1213, 1345, 1138, 1320, 1342, 1319, 1183, 1256,
        1157) -- confirming names are NOT embedded per-record in this
        response for most rows (the one exception is coincidental to
        that specific capture's context, not a general per-record
        field). A caller wanting a display name for a given activity_id
        would need a separate id->name lookup (e.g. findActivities,
        which DOES map names to ids for catalog activities -- but has
        its own unresolved field-layout ambiguity, see the module-level
        comment above _parse_recent_exercises_response) or would need to
        treat the entry as nameless/generic. This method returns
        activity_id but deliberately does NOT return a fabricated or
        guessed name field.

        Also NOTE: this endpoint returned exactly 14 records for a real
        account with history going back further than that -- it appears
        to be a LIMITED/recent-window endpoint, not a full historical
        export. For pulling a longer history, use export_exercises()'s
        CSV export instead (date-range based, same convention as
        export_servings() for the food diary) -- this method is best
        suited for the write-side "get a met_coefficient/activity_id I
        can reuse" lookup, not bulk historical sync.
        """
        if not self.nonce or not self.user_id:
            raise ValueError("client must be logged in before fetching exercise history")
        headers = {
            "Content-Type": GWT_CONTENT_TYPE,
            "x-gwt-module-base": GWT_MODULE_BASE,
            "x-gwt-permutation": GWT_PERMUTATION,
        }
        payload = (
            "7|0|6|https://cronometer.com/cronometer/|"
            f"{GWT_HEADER}|com.cronometer.shared.rpc.CronometerService|getRecentExercises|"
            f"java.lang.String/2004016611|{self.nonce}|1|2|3|4|1|5|6|"
        )
        resp = self.session.post(GWT_BASE_URL, headers=headers, data=payload, timeout=30)
        resp.raise_for_status()
        if not resp.text.startswith("//OK"):
            logger.error(f"Failed to fetch recent exercises: {resp.text}")
            return []
        return self._parse_recent_exercises_response(resp.text)

    def _parse_recent_exercises_response(self, raw: str) -> list[Dict[str, Any]]:
        """Split off //OK[ ... ] and parse the flat value array into
        structured records (20 raw values per record, anchored on
        self.user_id at a fixed position within each chunk -- confirmed
        via a real 14-record response). Isolated as its own method so
        the parsing logic (not the HTTP call) is what a unit test
        exercises against real captured response text.

        Does NOT attempt to resolve activity names -- see
        get_recent_exercises()'s docstring for why that's a known,
        deliberate gap rather than a guess."""
        body = raw[len("//OK["):-1] if raw.startswith("//OK[") and raw.endswith("]") else raw[4:]
        table_start = body.find('[')
        numeric_part = body[:table_start].rstrip(',')
        values = []
        for tok in numeric_part.split(','):
            tok = tok.strip()
            if not tok:
                continue
            if tok.startswith('"') and tok.endswith('"'):
                values.append(tok[1:-1])
                continue
            try:
                values.append(int(tok))
            except ValueError:
                values.append(float(tok))

        anchor = int(self.user_id)
        anchors = [i for i, v in enumerate(values) if v == anchor]
        records = []
        for a in anchors:
            start = a - 1  # userId confirmed at position 1 within each 20-wide chunk
            if start < 0 or start + 20 > len(values):
                continue
            chunk = values[start:start + 20]
            met_coefficient = chunk[0]
            duration_minutes = chunk[7]
            year, month, dom = chunk[11], chunk[12], chunk[13]
            calories_burned = -chunk[15]
            activity_id = chunk[18]
            records.append({
                "activity_id": activity_id,
                "met_coefficient": met_coefficient,
                "duration_minutes": duration_minutes,
                "calories_burned": calories_burned,
                "date": f"{year:04d}-{month:02d}-{dom:02d}",
            })
        return records

    def export(self, export_type: str, start_date: str, end_date: str) -> str:
        """
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


# Exercises CSV column names -- CONFIRMED against a real exported file
# from this account (exercises (2).csv, 15 real rows spanning
# 2026-07-15 to 2026-07-21, including real device-synced entries like
# "Active Energy Balance (Apple Health)" and "Traditional Strength
# Training (Apple Health)" alongside manually-logged ones like "Martial
# Arts" and "Custom Exercise"). Real header: Day,Group,Exercise,Minutes,
# Calories Burned -- an extra `Group` column exists (always
# "Uncategorized" in the real sample) that isn't used by this parser but
# is preserved under its raw key like every other column.
#
# IMPORTANT, confirmed via the real file: "Calories Burned" is NEGATIVE
# in the CSV itself (e.g. -402.21, -245.07) -- exercise calorie burn is
# stored as a negative delta in Cronometer's own export, matching the
# same negative-delta convention already confirmed on the GWT-RPC write
# side (add_exercise() negates a positive input internally). This parser
# negates it back to positive so callers get calories_burned as a
# positive number consistently, matching ExerciseLogContract's own
# convention (see food_entry_contract.py) and add_exercise()'s input
# convention -- getting this sign wrong here would have silently stored
# every synced exercise entry with negative calories, which is exactly
# the kind of thing this project's tenets say to verify rather than
# assume, and was caught precisely because a real file was checked
# instead of trusting the initial corroborated-but-unconfirmed guess.
EXERCISE_CSV_CALORIES_COLUMN_CANDIDATES = [
    "Calories Burned",
    "Calories Burned (kcal)",
    "Energy (kcal)",
    "kcal",
]


def parse_exercises_csv(raw_csv: str) -> list[Dict[str, Any]]:
    """
    Parse the exercises export CSV into a list of dicts with normalized
    keys: {date, activity_name, duration_minutes, calories_burned}, plus
    every original column preserved under its raw CSV header too (same
    "parse everything, keep the source data too" approach
    parse_servings_csv already uses) -- so a caller can inspect the raw
    row if the normalized fields don't cover something needed later.

    Raises ValueError if the CSV's header doesn't contain a recognizable
    'Day'/'Exercise'/'Minutes' column, or none of
    EXERCISE_CSV_CALORIES_COLUMN_CANDIDATES matches -- surfacing a
    genuinely wrong assumption immediately (e.g. Cronometer renaming a
    column) rather than silently producing empty/zeroed records. The
    core column names (Day/Exercise/Minutes/Calories Burned) are
    confirmed against a real exported file from this account -- see the
    comment above EXERCISE_CSV_CALORIES_COLUMN_CANDIDATES.
    """
    reader = csv.DictReader(raw_csv.splitlines())
    if reader.fieldnames is None:
        return []

    fieldnames = set(reader.fieldnames)
    missing = [c for c in ("Day", "Exercise", "Minutes") if c not in fieldnames]
    if missing:
        raise ValueError(
            f"exercises CSV is missing expected column(s) {missing} -- header was "
            f"{reader.fieldnames!r}. This means the real export format differs from what "
            f"was previously confirmed against a real file and needs re-verifying, not "
            f"silently guessed further."
        )
    calories_col = next((c for c in EXERCISE_CSV_CALORIES_COLUMN_CANDIDATES if c in fieldnames), None)
    if calories_col is None:
        raise ValueError(
            f"exercises CSV has no recognizable calories-burned column -- header was "
            f"{reader.fieldnames!r}, checked candidates {EXERCISE_CSV_CALORIES_COLUMN_CANDIDATES!r}."
        )

    rows = []
    for row in reader:
        parsed = {k: _try_parse_number(v) for k, v in row.items()}
        parsed["date"] = row.get("Day")
        parsed["activity_name"] = row.get("Exercise")
        parsed["duration_minutes"] = _try_parse_number(row.get("Minutes"))
        raw_calories = _try_parse_number(row.get(calories_col))
        # Confirmed via a real exported file: Cronometer stores this
        # NEGATIVE (a burn delta) -- negate back to positive so callers
        # get a consistent, positive calories_burned regardless of
        # source (matches ExerciseLogContract's and add_exercise()'s
        # convention). abs() rather than a plain negation in case a
        # future export ever has a stray positive value for some other
        # reason -- either way "burned calories" should read as
        # positive.
        parsed["calories_burned"] = abs(raw_calories) if isinstance(raw_calories, (int, float)) else raw_calories
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
