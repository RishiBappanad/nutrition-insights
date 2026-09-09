"""
Unified food search across USDA FoodData Central and Canadian Nutrient File.
"""

import os
import requests
import logging
from typing import List, Dict, Optional

from app.food_category import map_raw_category

logger = logging.getLogger(__name__)

USDA_BASE = "https://api.nal.usda.gov/fdc/v1"
USDA_API_KEY = os.getenv("USDA_API_KEY", "DEMO_KEY")
if USDA_API_KEY == "DEMO_KEY":
    # DEMO_KEY was hardcoded here with a "replace for production" comment
    # that nobody ever acted on -- production has been silently running on
    # it this whole time (confirmed 2026-09-09: no USDA_API_KEY exists in
    # Secret Manager or on the Cloud Run service). DEMO_KEY's rate limit
    # is a handful of requests per minute per IP, well below real usage --
    # USDA starts returning 429s (caught below and turned into empty
    # results, not a visible error) after just a few searches, which is
    # exactly the "search works for a few queries then silently returns
    # nothing" symptom this was root-caused from. Warning loudly here so
    # this can't silently persist unnoticed the way it did before -- not
    # failing outright like JWT_SECRET does, since search still partially
    # works without a real key and this isn't a security-sensitive value.
    logger.warning(
        "USDA_API_KEY is not set -- falling back to the public DEMO_KEY, "
        "which USDA rate-limits after a handful of requests. Get a free "
        "key at https://fdc.nal.usda.gov/api-key-signup.html and set "
        "USDA_API_KEY."
    )

CNF_BASE = "https://food-nutrition.canada.ca/api/canadian-nutrient-file"


KCAL_PER_KJ = 1 / 4.184


def _normalize_energy(raw_nutrients: List[Dict]) -> Dict:
    """Build a {name: {value, unit}} dict from a USDA foodNutrients list,
    collapsing Energy into one canonical "Energy" (KCAL) entry.

    USDA FDC reports Energy twice per food under the exact same
    nutrientName -- once in kcal, once in kJ. A naive `nutrients[name] =
    ...` loop silently drops one of the two, non-deterministically
    depending on array order, since the second one processed overwrites
    the first at the same dict key -- this was the root cause of some
    foods showing 0 calories (whichever survived happened to be the kJ
    entry) despite other macros coming through fine. Always resolve to
    the real kcal value when present; fall back to converting from kJ
    (a fixed, exact one-shot conversion, not an approximation) only if
    USDA didn't report a kcal entry at all for this food."""
    nutrients = {}
    energy_kcal = None
    energy_kj = None
    for n in raw_nutrients:
        name = n.get("nutrientName", "")
        value = n.get("value", 0)
        unit = (n.get("unitName") or "").upper()
        if name == "Energy":
            if unit == "KCAL":
                energy_kcal = value
            elif unit == "KJ":
                energy_kj = value
            continue
        nutrients[name] = {"value": value, "unit": unit}

    if energy_kcal is not None:
        nutrients["Energy"] = {"value": energy_kcal, "unit": "KCAL"}
    elif energy_kj is not None:
        nutrients["Energy"] = {"value": round(energy_kj * KCAL_PER_KJ, 1), "unit": "KCAL"}

    return nutrients


def search_usda(query: str, page_size: int = 10) -> List[Dict]:
    """Search USDA FoodData Central. Returns normalized results."""
    try:
        resp = requests.get(f"{USDA_BASE}/foods/search", params={
            "api_key": USDA_API_KEY,
            "query": query,
            "pageSize": page_size,
            "dataType": "SR Legacy,Foundation,Branded"
        }, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        results = []
        for food in data.get("foods", []):
            nutrients = _normalize_energy(food.get("foodNutrients", []))

            food_name = food["description"]
            results.append({
                "source": "USDA",
                "id": str(food["fdcId"]),
                "name": food_name,
                "brand": food.get("brandName", ""),
                # Resolved to our fixed enum here, not just passed
                # through raw -- see app/food_category.py. A food whose
                # raw USDA category doesn't map to anything we recognize
                # gets None, not a fabricated guess.
                "category": map_raw_category(food.get("foodCategory", ""), food_name),
                "nutrients": nutrients,
                "serving_size": food.get("servingSize"),
                "serving_unit": food.get("servingSizeUnit", "g"),
            })
        return results
    except Exception as e:
        logger.error(f"USDA search failed: {e}")
        return []


def search_cnf(query: str) -> List[Dict]:
    """Search Canadian Nutrient File. Returns normalized results."""
    try:
        # CNF doesn't have a search endpoint — fetch all foods and filter locally
        # For production, cache this list
        resp = requests.get(f"{CNF_BASE}/food/?lang=en&type=json", timeout=15)
        resp.raise_for_status()
        all_foods = resp.json()

        # Simple case-insensitive search
        query_lower = query.lower()
        matches = [f for f in all_foods if query_lower in f.get("food_description", "").lower()][:10]

        results = []
        for food in matches:
            # Fetch nutrients for each match
            food_code = food.get("food_code")
            nutrients = _get_cnf_nutrients(food_code)

            results.append({
                "source": "CNF",
                "id": str(food_code),
                "name": food.get("food_description", ""),
                "brand": "",
                # CNF's food-list endpoint never actually includes a
                # food_group field (confirmed against a real response --
                # the old `food.get("food_group", {})` lookup here was
                # always silently returning {}, meaning "category" was
                # always ""). Rather than keep pretending, this is
                # honestly None until a real CNF category source is found.
                "category": None,
                "nutrients": nutrients,
                "serving_size": 100,
                "serving_unit": "g",
            })
        return results
    except Exception as e:
        logger.error(f"CNF search failed: {e}")
        return []


def _get_cnf_nutrients(food_code) -> Dict:
    """Fetch nutrient data for a CNF food.

    CNF reports energy as two separate named nutrients, "Energy (kcal)"
    and "Energy (kJ)" (confirmed against a real API response) -- unlike
    USDA, these don't collide in the dict since the names differ. But
    the frontend's calorie extraction (food-log.jsx's extractMacro)
    looks for a nutrient named exactly "Energy", the same canonical key
    _normalize_energy() above produces for USDA results -- CNF's
    "Energy (kcal)" never matched that, so every CNF-sourced food
    silently showed 0 calories regardless of the kJ/kcal question.
    Normalized here the same way USDA is, so both sources produce the
    same shape and the frontend needs no source-specific logic."""
    try:
        resp = requests.get(
            f"{CNF_BASE}/nutrientamount/?lang=en&type=json&id={food_code}",
            timeout=10
        )
        resp.raise_for_status()
        data = resp.json()

        nutrients = {}
        energy_kcal = None
        energy_kj = None
        for item in data:
            name = item.get("nutrient_web_name", "")
            value = item.get("nutrient_value", 0)
            if name == "Energy (kcal)":
                energy_kcal = value
                continue
            if name == "Energy (kJ)":
                energy_kj = value
                continue
            if name and value:
                nutrients[name] = {"value": value, "unit": ""}

        if energy_kcal is not None:
            nutrients["Energy"] = {"value": energy_kcal, "unit": "KCAL"}
        elif energy_kj is not None:
            nutrients["Energy"] = {"value": round(energy_kj * KCAL_PER_KJ, 1), "unit": "KCAL"}

        return nutrients
    except Exception as e:
        logger.error(f"CNF nutrient fetch failed for {food_code}: {e}")
        return {}


def search_foods(query: str, sources: Optional[List[str]] = None) -> List[Dict]:
    """
    Search across all configured food databases.
    
    Args:
        query: Food search term
        sources: List of sources to search ["USDA", "CNF"]. Default: all.
    
    Returns:
        Combined list of food results from all sources.
    """
    if sources is None:
        sources = ["USDA", "CNF"]

    results = []
    if "USDA" in sources:
        results.extend(search_usda(query))
    if "CNF" in sources:
        results.extend(search_cnf(query))

    return results
