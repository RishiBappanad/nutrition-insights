"""
Unified food search across USDA FoodData Central and Canadian Nutrient File.
"""

import requests
import logging
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

USDA_BASE = "https://api.nal.usda.gov/fdc/v1"
USDA_API_KEY = "DEMO_KEY"  # Replace with real key for production

CNF_BASE = "https://food-nutrition.canada.ca/api/canadian-nutrient-file"


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
            nutrients = {}
            for n in food.get("foodNutrients", []):
                name = n.get("nutrientName", "")
                value = n.get("value", 0)
                unit = n.get("unitName", "")
                nutrients[name] = {"value": value, "unit": unit}

            results.append({
                "source": "USDA",
                "id": str(food["fdcId"]),
                "name": food["description"],
                "brand": food.get("brandName", ""),
                "category": food.get("foodCategory", ""),
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
                "category": food.get("food_group", {}).get("food_group_name", ""),
                "nutrients": nutrients,
                "serving_size": 100,
                "serving_unit": "g",
            })
        return results
    except Exception as e:
        logger.error(f"CNF search failed: {e}")
        return []


def _get_cnf_nutrients(food_code) -> Dict:
    """Fetch nutrient data for a CNF food."""
    try:
        resp = requests.get(
            f"{CNF_BASE}/nutrientamount/?lang=en&type=json&id={food_code}",
            timeout=10
        )
        resp.raise_for_status()
        data = resp.json()

        nutrients = {}
        for item in data:
            name = item.get("nutrient_web_name", "")
            value = item.get("nutrient_value", 0)
            if name and value:
                nutrients[name] = {"value": value, "unit": ""}
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
