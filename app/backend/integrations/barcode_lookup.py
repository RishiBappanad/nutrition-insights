"""
Resolves a scanned barcode (UPC/EAN) to product nutrition data via Open
Food Facts' public API -- the standard free, keyless barcode-to-product
database. Neither of this app's existing sources (food_search.py's USDA/
CNF) support barcode lookup: USDA has a gtinUpc field on branded foods but
no query-by-GTIN endpoint, and CNF has no barcode concept at all.

Deliberately NOT one of search_foods()'s sources -- a barcode match is a
lead to reconcile against USDA/CNF (via app/food_matching.py), not a third
database to search interchangeably, per this app's existing rule that a
scanned food should link to an existing USDA/CNF entry whenever one exists.
"""

import logging
from typing import Dict, Optional

import requests

logger = logging.getLogger(__name__)

OFF_BASE = "https://world.openfoodfacts.org/api/v2/product"

# Open Food Facts reports per-100g values under its own key names -- mapped
# onto this app's standard USDA-style nutrient names (the same convention
# label_scanner.py's Gemini prompt already uses) so a barcode-resolved food
# flows through the same display/storage code as every other source with
# no source-specific handling downstream.
_OFF_NUTRIENT_MAP = {
    "energy-kcal_100g": ("Energy", "KCAL"),
    "proteins_100g": ("Protein", "G"),
    "carbohydrates_100g": ("Carbohydrate, by difference", "G"),
    "fat_100g": ("Total lipid (fat)", "G"),
    "fiber_100g": ("Fiber, total dietary", "G"),
    "sugars_100g": ("Sugars, total", "G"),
    "sodium_100g": ("Sodium, Na", "MG"),
}


def _normalize_off_nutrients(nutriments: Dict) -> Dict:
    nutrients = {}
    for off_key, (name, unit) in _OFF_NUTRIENT_MAP.items():
        value = nutriments.get(off_key)
        if value is None:
            continue
        # OFF reports sodium in grams per 100g; this app's convention
        # (matching USDA's "Sodium, Na") is milligrams.
        if off_key == "sodium_100g":
            value = value * 1000
        nutrients[name] = {"value": round(value, 2), "unit": unit}
    return nutrients


def lookup_barcode(barcode: str) -> Optional[Dict]:
    """Looks up a barcode via Open Food Facts. Returns a dict shaped like
    food_search.py's results (source="OpenFoodFacts") on a found product
    with a name, or None if not found, unreadable, or the API is
    unreachable -- never raises, matching this app's other integrations'
    graceful-degradation contract."""
    try:
        resp = requests.get(f"{OFF_BASE}/{barcode}.json", timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.error(f"Open Food Facts lookup failed for barcode {barcode}: {e}")
        return None

    if data.get("status") != 1:
        return None

    product = data.get("product") or {}
    name = product.get("product_name") or product.get("generic_name")
    if not name:
        return None

    return {
        "source": "OpenFoodFacts",
        "id": barcode,
        "name": name,
        "brand": product.get("brands") or "",
        # OFF's category taxonomy doesn't map onto this app's fixed
        # FoodCategory enum (see app/food_category.py) -- honestly None
        # rather than a fabricated guess, same stance food_search.py
        # takes for CNF foods it can't categorize either.
        "category": None,
        "nutrients": _normalize_off_nutrients(product.get("nutriments") or {}),
        "serving_size": product.get("serving_quantity"),
        "serving_unit": product.get("serving_quantity_unit") or "g",
    }
