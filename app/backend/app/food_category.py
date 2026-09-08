"""Food-type category: one fixed enum per food/recipe/pantry item,
distinct from `meal` (Breakfast/Lunch/Dinner/Snack -- when something was
eaten, already a plain column on food_log) and from nutrient_facts'
open-ended named-attribute pattern (category is one fixed-shape scalar
field, always named "category", always one of the FoodCategory values
below -- not a set of named attributes, so it doesn't fit that
polymorphic table's value+unit shape). Storage is a plain `category
TEXT` column wherever a food-like item lives (food_log, custom_foods,
pantry_items, recipes, recipe_items, meals, meal_items); this module is
the one place the enum, the raw-source mapping, and the
dominant-ingredient logic live, so that duplication doesn't happen at
the LOGIC level even though it can't be avoided at the schema level for
a single scalar field.

Per workspace-notes/EVENT_CONTRACT_SPEC.md's "Category: one fixed enum
per tracker" (drafted, this module is what actually implements it).
"""
from enum import Enum
from typing import Optional


class FoodCategory(str, Enum):
    """A str-mixin Enum -- members compare equal to and serialize as
    their plain string value (`FoodCategory.PRODUCE == "produce"`), so
    they drop straight into asyncpg query params, JSON responses, and
    the `category TEXT` columns without any extra conversion, while
    still giving real enum type-safety (a typo'd category name fails at
    `FoodCategory("produce_typo")` instead of silently becoming a new,
    unrecognized string value in the database)."""

    PRODUCE = "produce"
    PROTEIN = "protein"
    DAIRY = "dairy"
    GRAINS_STARCHES = "grains_starches"
    LEGUMES_NUTS = "legumes_nuts"
    FATS_OILS = "fats_oils"
    BEVERAGES = "beverages"
    ALCOHOL = "alcohol"
    SNACKS_SWEETS = "snacks_sweets"
    PREPARED_RESTAURANT = "prepared_restaurant"


# Default for a composite/user-created item with no ingredient data to
# infer from (e.g. a custom food with no source), matching the spec's
# "Default for recipes/meals/custom foods" language.
DEFAULT_CATEGORY = FoodCategory.PREPARED_RESTAURANT

# USDA FoodData Central's foodCategory strings -> our enum. Cronometer's
# own CSV "Category" column reuses this same taxonomy (confirmed real
# value "Fast Foods" against an actual export, see EVENT_CONTRACT_SPEC.md)
# -- the same mapping applies to both sources, not two separate tables.
# Matching is case-insensitive and trims whitespace. Built from USDA's
# published, stable category list plus the groupings EVENT_CONTRACT_SPEC.md
# already drafted -- not independently re-confirmed against a live API
# response for every single value this session (USDA's DEMO_KEY was
# rate-limited throughout), so treat entries beyond the spec's own
# confirmed "Fast Foods" example as best-effort, not verified.
_RAW_CATEGORY_MAP: dict[str, FoodCategory] = {
    "fruits and fruit juices": FoodCategory.PRODUCE,
    "vegetables and vegetable products": FoodCategory.PRODUCE,
    "beef products": FoodCategory.PROTEIN,
    "pork products": FoodCategory.PROTEIN,
    "poultry products": FoodCategory.PROTEIN,
    "finfish and shellfish products": FoodCategory.PROTEIN,
    "lamb, veal, and game products": FoodCategory.PROTEIN,
    "sausages and luncheon meats": FoodCategory.PROTEIN,
    "dairy and egg products": FoodCategory.DAIRY,  # overridden to protein for eggs, see map_raw_category
    "cereal grains and pasta": FoodCategory.GRAINS_STARCHES,
    "baked products": FoodCategory.GRAINS_STARCHES,
    "breakfast cereals": FoodCategory.GRAINS_STARCHES,
    "legumes and legume products": FoodCategory.LEGUMES_NUTS,
    "nut and seed products": FoodCategory.LEGUMES_NUTS,
    "fats and oils": FoodCategory.FATS_OILS,
    "beverages": FoodCategory.BEVERAGES,
    "alcoholic beverages": FoodCategory.ALCOHOL,
    "snacks": FoodCategory.SNACKS_SWEETS,
    "sweets": FoodCategory.SNACKS_SWEETS,
    "fast foods": FoodCategory.PREPARED_RESTAURANT,
    "restaurant foods": FoodCategory.PREPARED_RESTAURANT,
    "meals, entrees, and side dishes": FoodCategory.PREPARED_RESTAURANT,
    "soups, sauces, and gravies": FoodCategory.PREPARED_RESTAURANT,
}


def map_raw_category(raw: Optional[str], food_name: str = "") -> Optional[FoodCategory]:
    """Map a raw USDA/Cronometer category string to our enum. Returns
    None (not a fabricated guess) for an empty or unrecognized raw
    value -- an unmapped category is a real, honest gap, not something
    to silently default away.

    Egg special case: USDA's "Dairy and Egg Products" covers both milk
    and eggs under one category, but the spec explicitly calls for eggs
    to land in `protein`, not `dairy` -- distinguished by checking for
    "egg" in the food name, since the raw category string alone can't
    tell the two apart.
    """
    if not raw:
        return None
    key = raw.strip().lower()
    mapped = _RAW_CATEGORY_MAP.get(key)
    if mapped == FoodCategory.DAIRY and "egg" in food_name.lower():
        return FoodCategory.PROTEIN
    return mapped


def dominant_category_by_calories(items: list[dict]) -> Optional[FoodCategory]:
    """Given a recipe/meal's items as [{"category": FoodCategory|str|None, "calories": float}, ...],
    return the category of whichever items together contribute the most
    total calories -- the default for a composite item's own category,
    per explicit user decision (2026-09-08): calories, not ingredient
    count or weight, since that's the dimension that matters for this
    use case. Items with no category are excluded from the calorie
    totals (an uncategorized ingredient doesn't get to silently win by
    default). Returns None if no item has both a category and positive
    calories -- caller decides the fallback (e.g. DEFAULT_CATEGORY) in
    that case, this function never fabricates one itself.
    """
    totals: dict[FoodCategory, float] = {}
    for item in items:
        category = item.get("category")
        calories = item.get("calories") or 0
        if not category or calories <= 0:
            continue
        category = FoodCategory(category)
        totals[category] = totals.get(category, 0) + calories

    if not totals:
        return None
    return max(totals, key=lambda c: totals[c])


def resolve_category(
    explicit: Optional[FoodCategory],
    items: list[dict],
    existing: Optional[dict] = None,
) -> tuple[FoodCategory, bool]:
    """The one place "what category should this composite item (recipe
    or meal) get, and is that a user override or an auto-computed
    default" is decided -- every router that owns a composite-item table
    (recipes.py, meals.py, food_entry_contract.py's import_recipe) calls
    this instead of each re-implementing the same branching, which is
    exactly what happened once already before this function existed
    (see workspace-notes/ACTION_ITEMS.md's 2026-09-08 entry).

    Rule, in order:
      1. `explicit` (a value the caller's request itself set) always wins.
      2. Otherwise, `existing` (the row's current {"category",
         "category_is_custom"} before this save, or None for a brand new
         item) is checked: if it was already a user override, that
         override is preserved rather than silently recomputed out from
         under them.
      3. Otherwise, recompute via dominant_category_by_calories(items),
         falling back to DEFAULT_CATEGORY if that returns None (no item
         has both a category and positive calories).

    Returns (category, category_is_custom) -- the exact two values every
    caller writes straight into its own `category`/`category_is_custom`
    columns.
    """
    if explicit is not None:
        return explicit, True
    if existing is not None and existing.get("category_is_custom"):
        return existing.get("category"), True
    return dominant_category_by_calories(items) or DEFAULT_CATEGORY, False
