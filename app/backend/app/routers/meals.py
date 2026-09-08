"""Custom meals API: a simple named collection of items, logged together
at face value. Deliberately simpler than recipes (routers/recipes.py) --
no servings-per-batch, no scaling, no pantry availability check. A meal
is just "these items, saved together, logged all at once" (e.g. "My usual
breakfast" = eggs + toast + coffee, always logged as that exact trio)."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..routers.auth import get_current_user
from ..db.meals import query as meals_query
from ..food_category import FoodCategory, resolve_category

router = APIRouter()


class MealItemRequest(BaseModel):
    food_name: str
    source: Optional[str] = None
    source_id: Optional[str] = None
    category: Optional[FoodCategory] = None
    serving_size: float = 1.0
    serving_unit: str = "serving"
    # `calories` is the sole top-level numeric field. Protein/carbs/fat/
    # fiber belong in nutrients under their standard USDA names.
    calories: float = 0
    nutrients: dict = {}


class MealRequest(BaseModel):
    name: str
    # Same resolve rule as recipes.py's RecipeRequest.category: leave
    # unset to auto-compute as whichever category contributes the most
    # total calories across `items`; set explicitly to override. On
    # update, an earlier explicit override is preserved unless this
    # request itself sets a new one.
    category: Optional[FoodCategory] = None
    items: list[MealItemRequest] = []


class LogMealRequest(BaseModel):
    date: str
    meal: str = "Lunch"
    combined: bool = False
    """False (default): log every item individually (Cronometer calls
    this exploding a meal — the existing behavior, unchanged). True: log
    the meal as ONE aggregated food_log entry (source='meal',
    source_id=<meal id>), matching how Cronometer treats a meal as "its
    own food" until the user explicitly explodes it (see
    cronometer.com/blog/custom-meals and how-to-recipes — the same
    explode concept recipes have). A combined entry can later be split
    into its per-item entries via POST /meals/{id}/explode/{food_log_id}."""


# food.py's _search_user_meals imports this directly (`from .meals import
# _get_meal_with_items`) -- kept as an alias so that import keeps working
# unchanged now that the real implementation lives in db/meals/query.py.
_get_meal_with_items = meals_query.get_meal_with_items


@router.post("")
async def create_meal(req: MealRequest, user_id: int = Depends(get_current_user)):
    resolved_category, category_is_custom = resolve_category(
        req.category, [item.model_dump() for item in req.items],
    )
    meal_id = await meals_query.create_meal(user_id, req.name, resolved_category, category_is_custom, req.items)
    return {"status": "created", "id": meal_id}


@router.get("")
async def list_meals(user_id: int = Depends(get_current_user)):
    rows = await meals_query.list_meals(user_id)
    return {"meals": [{"id": r["id"], "name": r["name"], "category": r["category"]} for r in rows]}


@router.get("/{meal_id}")
async def get_meal(meal_id: int, user_id: int = Depends(get_current_user)):
    meal, items = await meals_query.get_meal_with_items_new_conn(meal_id, user_id)
    if meal is None:
        raise HTTPException(status_code=404, detail="Meal not found")
    return {
        "id": meal["id"], "name": meal["name"], "category": meal["category"],
        "category_is_custom": meal["category_is_custom"], "items": items,
    }


@router.put("/{meal_id}")
async def update_meal(meal_id: int, req: MealRequest, user_id: int = Depends(get_current_user)):
    existing = await meals_query.get_meal_for_update(meal_id, user_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Meal not found")
    resolved_category, category_is_custom = resolve_category(
        req.category, [item.model_dump() for item in req.items], dict(existing),
    )
    await meals_query.update_meal(meal_id, req.name, resolved_category, category_is_custom, req.items)
    return {"status": "updated"}


@router.delete("/{meal_id}")
async def delete_meal(meal_id: int, user_id: int = Depends(get_current_user)):
    """meal_items still has a real FK (ON DELETE CASCADE) to meals, so
    deleting a meal auto-deletes its items -- but their nutrient_facts
    rows don't cascade (nutrient_facts has no FK at all, see
    app/nutrient_facts.py), so those are cleared explicitly first."""
    await meals_query.delete_meal(meal_id, user_id)
    return {"status": "deleted"}


@router.post("/{meal_id}/log")
async def log_meal(meal_id: int, req: LogMealRequest, user_id: int = Depends(get_current_user)):
    """
    Two logging modes (see LogMealRequest.combined docstring):

    combined=false (default): logs every item in the meal as its own
    food_log entry, all with the same date/meal — matches Cronometer's
    "exploded" view. Each item still shows individually in the diary and
    can be edited/deleted independently afterward.

    combined=true: logs the whole meal as ONE aggregated food_log entry
    (source='meal', source_id=<meal id>) — the meal behaves like its own
    single food, matching how a recipe serving logs as one entry. Use
    POST /meals/{meal_id}/explode/{food_log_id} afterward to convert that
    one entry back into its per-item entries, if wanted later.
    """
    meal, items = await meals_query.get_meal_with_items_new_conn(meal_id, user_id)
    if meal is None:
        raise HTTPException(status_code=404, detail="Meal not found")

    if req.combined:
        macros = {"calories": 0.0}
        nutrients: dict = {}
        for item in items:
            macros["calories"] += item.get("calories", 0) or 0
            # Protein/carbs/fat/fiber are summed here along with every
            # other non-macro nutrient.
            for name, info in item.get("nutrients", {}).items():
                bucket = nutrients.setdefault(name, {"value": 0.0, "unit": info["unit"]})
                bucket["value"] += info["value"]

        food_log_id = await meals_query.insert_combined_meal_log(
            user_id, req.date, req.meal, meal["name"], str(meal_id), meal["category"],
            macros["calories"], nutrients,
        )
        return {"status": "logged", "food_log_ids": [food_log_id], "combined": True}

    food_log_ids = await meals_query.insert_exploded_meal_items_log(user_id, req.date, req.meal, items)
    return {"status": "logged", "food_log_ids": food_log_ids, "combined": False}


@router.post("/{meal_id}/explode/{food_log_id}")
async def explode_meal_entry(meal_id: int, food_log_id: int, user_id: int = Depends(get_current_user)):
    """
    Convert a combined meal food_log entry (created by
    POST /meals/{id}/log with combined=true) back into its per-item
    entries — matches Cronometer's "explode" action on a logged recipe/
    meal. Deletes the one combined entry and inserts one food_log entry
    per meal item, using the SAME date/meal the combined entry had.

    404s if food_log_id doesn't exist, isn't owned by this user, isn't
    source='meal', or doesn't reference this meal_id — exploding the
    wrong entry (e.g. a different meal's combined log) would silently
    corrupt an unrelated diary entry, so all four are checked explicitly
    rather than trusting the caller's meal_id/food_log_id pairing.
    """
    entry = await meals_query.get_combined_meal_log_entry(food_log_id, user_id, meal_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Combined meal entry not found for this meal_id/food_log_id")

    meal, items = await meals_query.get_meal_with_items_new_conn(meal_id, user_id)
    if meal is None:
        raise HTTPException(status_code=404, detail="Meal not found")

    food_log_ids = await meals_query.replace_combined_entry_with_items(
        food_log_id, user_id, entry["date"], entry["meal"], items,
    )
    return {"status": "exploded", "food_log_ids": food_log_ids}
