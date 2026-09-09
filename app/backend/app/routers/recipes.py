"""Recipes API: aggregate food items into a batch, with servings-per-batch
so logging "1 serving" divides the aggregated total accordingly. Includes
a pantry "can I make this?" check (per user steering: recipes should let
a user look back later and confirm they have all ingredients before
cooking) and a log-to-diary action that scales by servings consumed.

Distinct from meals (routers/meals.py): a recipe's whole point is batch
division (a lasagna makes 6 servings, you eat 1). A meal has no batch
concept — it's just a flat group of items logged at face value."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..routers.auth import get_current_user
from ..db.recipes import query as recipes_query
from ..portion_scaling import scale_macros, scale_nutrients
from ..nutrient_groups import order_nutrients
from ..food_category import FoodCategory, resolve_category

router = APIRouter()


class RecipeItemRequest(BaseModel):
    food_name: str
    source: Optional[str] = None
    source_id: Optional[str] = None
    # Food-type category (produce/protein/dairy/etc) for this ingredient
    # -- feeds the recipe's own dominant-by-calories default, see
    # RecipeRequest.category below.
    category: Optional[FoodCategory] = None
    amount_grams: Optional[float] = None
    amount_multiple: Optional[float] = None
    # `calories` is the sole top-level numeric field (TrackStack's
    # "amount" for this tracker). Protein/carbs/fat/fiber are not
    # top-level fields — they belong in nutrients under their standard
    # USDA names ("Protein", "Carbohydrate, by difference", "Total lipid
    # (fat)", "Fiber, total dietary") like every other nutrient.
    calories: float = 0
    nutrients: dict = {}


class RecipeRequest(BaseModel):
    name: str
    servings_per_batch: float = 1.0
    # Leave unset to auto-compute as whichever category contributes the
    # most total calories across `items` (explicit user decision,
    # 2026-09-08); set explicitly to override that default. On update,
    # omitting this preserves whatever the recipe already has (an
    # earlier override stays an override; an earlier auto-computed value
    # gets recomputed from the new items) -- see update_recipe.
    category: Optional[FoodCategory] = None
    items: list[RecipeItemRequest] = []


class LogRecipeRequest(BaseModel):
    date: str
    meal: str = "Lunch"
    servings: float = 1.0


@router.post("")
async def create_recipe(req: RecipeRequest, user_id: int = Depends(get_current_user)):
    """Delegates to food_entry_contract.import_recipe() — the same shared
    function any recipe-import source (a future Cronometer recipe
    importer, once addFood's schema is decoded, or any other source)
    would call, so this endpoint and every import path share one
    insert implementation rather than maintaining duplicate logic."""
    if req.servings_per_batch <= 0:
        raise HTTPException(status_code=400, detail="servings_per_batch must be positive")
    from ..food_entry_contract import RecipeImportContract, RecipeItemContract, import_recipe

    contract = RecipeImportContract(
        name=req.name,
        servings_per_batch=req.servings_per_batch,
        category=req.category,
        items=[RecipeItemContract(**item.model_dump()) for item in req.items],
    )
    recipe_id = await import_recipe(user_id, contract)
    return {"status": "created", "id": recipe_id}


@router.get("")
async def list_recipes(user_id: int = Depends(get_current_user)):
    rows = await recipes_query.list_recipes(user_id)
    return {
        "recipes": [
            {"id": r["id"], "name": r["name"], "servings_per_batch": r["servings_per_batch"], "category": r["category"]}
            for r in rows
        ]
    }


# food.py's _search_user_recipes imports this directly (`from .recipes
# import _get_recipe_with_items`) -- kept as an alias so that import keeps
# working unchanged now that the real implementation lives in
# db/recipes/query.py.
_get_recipe_with_items = recipes_query.get_recipe_with_items


def _aggregate_batch_totals(items: list[dict]) -> dict:
    """Sum every item's macros + nutrients into one batch-level total —
    the "whole recipe" nutrition, before dividing by servings_per_batch."""
    macros = {"calories": 0.0}
    nutrients: dict[str, dict] = {}
    for item in items:
        macros["calories"] += item.get("calories", 0) or 0
        # Protein/carbs/fat/fiber are summed here along with every other
        # non-macro nutrient — calories is the only field with its own
        # dedicated column on a recipe item.
        for name, info in item.get("nutrients", {}).items():
            bucket = nutrients.setdefault(name, {"value": 0.0, "unit": info["unit"]})
            bucket["value"] += info["value"]
    return {"macros": macros, "nutrients": order_nutrients(nutrients)}


@router.get("/{recipe_id}")
async def get_recipe(recipe_id: int, user_id: int = Depends(get_current_user)):
    """Returns the recipe, its items, batch-level totals (sum of all
    items), and per-serving totals (batch totals / servings_per_batch) —
    the per-serving numbers are what a diary log actually applies."""
    recipe, items = await recipes_query.get_recipe_with_items_new_conn(recipe_id, user_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe not found")

    batch = _aggregate_batch_totals(items)
    per_serving_factor = 1.0 / recipe["servings_per_batch"]
    per_serving = {
        "macros": scale_macros(batch["macros"], per_serving_factor),
        "nutrients": scale_nutrients(batch["nutrients"], per_serving_factor),
    }
    return {
        "id": recipe["id"],
        "name": recipe["name"],
        "servings_per_batch": recipe["servings_per_batch"],
        "category": recipe["category"],
        "category_is_custom": recipe["category_is_custom"],
        "items": items,
        "batch_totals": batch,
        "per_serving_totals": per_serving,
    }


@router.put("/{recipe_id}")
async def update_recipe(recipe_id: int, req: RecipeRequest, user_id: int = Depends(get_current_user)):
    """Full replace of name/servings_per_batch/items — simpler and less
    error-prone than a partial-item-diff update for what's expected to be
    an infrequent edit (re-saving a whole recipe), matching how a user
    would naturally interact with a recipe editor (edit the whole thing,
    save)."""
    if req.servings_per_batch <= 0:
        raise HTTPException(status_code=400, detail="servings_per_batch must be positive")

    existing = await recipes_query.get_recipe_for_update(recipe_id, user_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Recipe not found")

    resolved_category, category_is_custom = resolve_category(
        req.category, [item.model_dump() for item in req.items], dict(existing),
    )
    await recipes_query.update_recipe(
        recipe_id, req.name, req.servings_per_batch, resolved_category, category_is_custom, req.items,
    )
    return {"status": "updated"}


@router.delete("/{recipe_id}")
async def delete_recipe(recipe_id: int, user_id: int = Depends(get_current_user)):
    """recipe_items still has a real FK (ON DELETE CASCADE) to recipes, so
    deleting a recipe auto-deletes its items -- but their nutrient_facts
    rows don't cascade (nutrient_facts has no FK at all, see
    app/nutrient_facts.py), so those are cleared explicitly first."""
    await recipes_query.delete_recipe(recipe_id, user_id)
    return {"status": "deleted"}


@router.post("/{recipe_id}/log")
async def log_recipe(recipe_id: int, req: LogRecipeRequest, user_id: int = Depends(get_current_user)):
    """Log N servings of a recipe to the diary as one food_log entry
    (named after the recipe), with macros/nutrients scaled from the
    per-serving totals by `servings` — matches a user eating "1.5
    servings of lasagna," not each ingredient being logged separately."""
    if req.servings <= 0:
        raise HTTPException(status_code=400, detail="servings must be positive")

    recipe, items = await recipes_query.get_recipe_with_items_new_conn(recipe_id, user_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe not found")

    batch = _aggregate_batch_totals(items)
    factor = req.servings / recipe["servings_per_batch"]
    macros = scale_macros(batch["macros"], factor)
    nutrients = scale_nutrients(batch["nutrients"], factor)

    food_log_id = await recipes_query.insert_recipe_log(
        user_id, req.date, req.meal, recipe["name"], str(recipe_id), recipe["category"],
        req.servings, macros["calories"], nutrients,
    )
    return {"status": "logged", "food_log_id": food_log_id}


@router.get("/{recipe_id}/can-make")
async def can_make_recipe(recipe_id: int, user_id: int = Depends(get_current_user)):
    """
    "Can I make this?" check against the pantry — per user request, a
    recipe should let someone look back later and confirm they have all
    ingredients before cooking, not just aggregate-and-log blindly.

    Matches recipe items to pantry items by (source, source_id) when both
    are set — the same identity USDA/CNF/custom foods already carry
    everywhere else. Items with no source (a recipe ingredient typed in
    freehand, e.g. "a pinch of salt") can't be matched against inventory
    at all and are reported separately as unmatchable, not silently
    treated as missing or present.

    For countable/single pantry items, availability also checks quantity
    (amount_grams/amount_multiple requested vs. remaining_servings) where
    that comparison is meaningful; bulk pantry items are only checked for
    presence, since bulk items don't track an exact quantity by design.
    """
    recipe, match = await recipes_query.can_make_recipe(recipe_id, user_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe not found")

    return {
        "recipe_id": recipe_id,
        "recipe_name": recipe["name"],
        "can_make": len(match["missing"]) == 0 and len(match["unmatchable"]) == 0,
        **match,
    }


@router.post("/{recipe_id}/make")
async def make_recipe(recipe_id: int, user_id: int = Depends(get_current_user)):
    """
    "Make it" — the pantry-consuming counterpart to can-make/log. Per
    explicit user request: making a recipe does NOT log it straight to
    the diary (a cooked batch usually isn't eaten all at once). Instead,
    in one transaction:
      1. Re-runs the same can-make matching this recipe's /can-make uses
         (via _match_recipe_against_pantry) and 400s with the same
         have/missing/unmatchable detail if anything's missing — makes
         it impossible to decrement pantry items the check itself would
         have flagged as insufficient.
      2. Decrements/removes each matched pantry item by its requested
         amount, using the EXACT SAME per-tracking_mode logic
         routers/pantry.py's /consume already uses (countable: decrement
         or delete at <=0; single: always delete; bulk: presence-only,
         never decremented — matches can-make's own bulk handling).
      3. Adds ONE new pantry item for the finished batch itself:
         source='recipe', source_id=<this recipe>, tracking_mode=
         'countable', remaining_servings=servings_per_batch, with
         per-serving nutrition (via _recipe_per_serving_nutrition,
         shared with food.py's recipe-as-search-result path so the two
         can't disagree on what "1 serving" of this recipe means).

    This means a made recipe becomes a normal pantry item afterward —
    reachable through the exact same /pantry list, consume, and remove
    flows every other pantry item already has, not a special case.
    """
    result = await recipes_query.make_recipe(recipe_id, user_id)
    if result.get("error") == "not_found":
        raise HTTPException(status_code=404, detail="Recipe not found")
    if result.get("error") == "missing_ingredients":
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Cannot make this recipe — missing or unmatchable ingredients",
                "have": result["have"], "missing": result["missing"], "unmatchable": result["unmatchable"],
            },
        )
    return {
        "status": "made",
        "pantry_item_id": result["pantry_item_id"],
        "servings_added": result["servings_added"],
        "ingredients_decremented": result["ingredients_decremented"],
        "ingredients_removed": result["ingredients_removed"],
    }
