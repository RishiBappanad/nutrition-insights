"""Recipes API: aggregate food items into a batch, with servings-per-batch
so logging "1 serving" divides the aggregated total accordingly. Includes
a pantry "can I make this?" check (per user steering: recipes should let
a user look back later and confirm they have all ingredients before
cooking) and a log-to-diary action that scales by servings consumed.

Distinct from meals (routers/meals.py): a recipe's whole point is batch
division (a lasagna makes 6 servings, you eat 1). A meal has no batch
concept — it's just a flat group of items logged at face value."""
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..routers.auth import get_current_user
from ..db import get_pool
from ..portion_scaling import scale_macros, scale_nutrients
from ..nutrient_facts import write_nutrients, read_nutrients_bulk, delete_nutrient_facts, delete_nutrient_facts_bulk
from ..nutrient_groups import order_nutrients

router = APIRouter()


class RecipeItemRequest(BaseModel):
    food_name: str
    source: Optional[str] = None
    source_id: Optional[str] = None
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
    items: list[RecipeItemRequest] = []


class LogRecipeRequest(BaseModel):
    date: str
    meal: str = "Lunch"
    servings: float = 1.0


async def _save_items(conn, recipe_id: int, items: list[RecipeItemRequest]):
    for item in items:
        item_id = await conn.fetchval(
            """INSERT INTO recipe_items (recipe_id, food_name, source, source_id, amount_grams, amount_multiple,
                   calories, nutrients_json)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
               RETURNING id""",
            recipe_id, item.food_name, item.source, item.source_id, item.amount_grams, item.amount_multiple,
            item.calories, json.dumps(item.nutrients),
        )
        await write_nutrients(conn, "recipe_item", item_id, item.nutrients)


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
        items=[RecipeItemContract(**item.model_dump()) for item in req.items],
    )
    recipe_id = await import_recipe(user_id, contract)
    return {"status": "created", "id": recipe_id}


@router.get("")
async def list_recipes(user_id: int = Depends(get_current_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, name, servings_per_batch, created_at, updated_at FROM recipes WHERE user_id = $1 ORDER BY name",
            user_id,
        )
    return {
        "recipes": [
            {"id": r["id"], "name": r["name"], "servings_per_batch": r["servings_per_batch"]}
            for r in rows
        ]
    }


async def _get_recipe_with_items(conn, recipe_id: int, user_id: int):
    recipe = await conn.fetchrow("SELECT * FROM recipes WHERE id = $1 AND user_id = $2", recipe_id, user_id)
    if recipe is None:
        return None, []
    item_rows = await conn.fetch("SELECT * FROM recipe_items WHERE recipe_id = $1 ORDER BY id", recipe_id)
    item_ids = [r["id"] for r in item_rows]
    nutrients_by_item = await read_nutrients_bulk(conn, "recipe_item", item_ids)
    items = []
    for r in item_rows:
        items.append({
            "id": r["id"],
            "food_name": r["food_name"],
            "source": r["source"],
            "source_id": r["source_id"],
            "amount_grams": r["amount_grams"],
            "amount_multiple": r["amount_multiple"],
            "calories": r["calories"],
            "nutrients": nutrients_by_item.get(r["id"], {}),
        })
    return recipe, items


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
    pool = await get_pool()
    async with pool.acquire() as conn:
        recipe, items = await _get_recipe_with_items(conn, recipe_id, user_id)
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
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            existing = await conn.fetchrow("SELECT id FROM recipes WHERE id = $1 AND user_id = $2", recipe_id, user_id)
            if existing is None:
                raise HTTPException(status_code=404, detail="Recipe not found")
            await conn.execute(
                "UPDATE recipes SET name = $1, servings_per_batch = $2, updated_at = now() WHERE id = $3",
                req.name, req.servings_per_batch, recipe_id,
            )
            old_item_ids = [r["id"] for r in await conn.fetch(
                "SELECT id FROM recipe_items WHERE recipe_id = $1", recipe_id
            )]
            await delete_nutrient_facts_bulk(conn, "recipe_item", old_item_ids)
            await conn.execute("DELETE FROM recipe_items WHERE recipe_id = $1", recipe_id)
            await _save_items(conn, recipe_id, req.items)
    return {"status": "updated"}


@router.delete("/{recipe_id}")
async def delete_recipe(recipe_id: int, user_id: int = Depends(get_current_user)):
    """recipe_items still has a real FK (ON DELETE CASCADE) to recipes, so
    deleting a recipe auto-deletes its items -- but their nutrient_facts
    rows don't cascade (nutrient_facts has no FK at all, see
    app/nutrient_facts.py), so those are cleared explicitly first."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Ownership-scoped join, not a bare recipe_id lookup -- same
            # reasoning as meals.py's delete_meal.
            item_ids = [r["id"] for r in await conn.fetch(
                """SELECT ri.id FROM recipe_items ri
                   JOIN recipes r ON r.id = ri.recipe_id
                   WHERE ri.recipe_id = $1 AND r.user_id = $2""",
                recipe_id, user_id,
            )]
            await delete_nutrient_facts_bulk(conn, "recipe_item", item_ids)
            await conn.execute("DELETE FROM recipes WHERE id = $1 AND user_id = $2", recipe_id, user_id)
    return {"status": "deleted"}


@router.post("/{recipe_id}/log")
async def log_recipe(recipe_id: int, req: LogRecipeRequest, user_id: int = Depends(get_current_user)):
    """Log N servings of a recipe to the diary as one food_log entry
    (named after the recipe), with macros/nutrients scaled from the
    per-serving totals by `servings` — matches a user eating "1.5
    servings of lasagna," not each ingredient being logged separately."""
    if req.servings <= 0:
        raise HTTPException(status_code=400, detail="servings must be positive")

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            recipe, items = await _get_recipe_with_items(conn, recipe_id, user_id)
            if recipe is None:
                raise HTTPException(status_code=404, detail="Recipe not found")

            batch = _aggregate_batch_totals(items)
            factor = req.servings / recipe["servings_per_batch"]
            macros = scale_macros(batch["macros"], factor)
            nutrients = scale_nutrients(batch["nutrients"], factor)

            food_log_id = await conn.fetchval(
                """INSERT INTO food_log (user_id, date, meal, food_name, source, source_id,
                       serving_size, serving_unit, calories, nutrients_json)
                   VALUES ($1, $2, $3, $4, 'recipe', $5, $6, 'serving', $7, $8)
                   RETURNING id""",
                user_id, req.date, req.meal, recipe["name"], str(recipe_id), req.servings,
                macros["calories"], json.dumps(nutrients),
            )
            await write_nutrients(conn, "food_log", food_log_id, nutrients)
    return {"status": "logged", "food_log_id": food_log_id}


async def _match_recipe_against_pantry(conn, items: list[dict], user_id: int) -> dict:
    """
    Shared matching logic for "can I make this?" (can_make_recipe) AND
    "make it" (make_recipe) — isolated here so the two can never
    silently disagree about what counts as available. Matches recipe
    items to pantry items by (source, source_id); see can_make_recipe's
    docstring for the full matching rules (countable/single quantity
    checks, bulk presence-only, unmatchable freehand items).
    """
    pantry_rows = await conn.fetch(
        "SELECT * FROM pantry_items WHERE user_id = $1 AND is_finished = FALSE", user_id
    )
    pantry_by_source = {(p["source"], p["source_id"]): p for p in pantry_rows if p["source"] and p["source_id"]}

    have, missing, unmatchable = [], [], []
    for item in items:
        key = (item["source"], item["source_id"])
        if not item["source"] or not item["source_id"]:
            unmatchable.append({"food_name": item["food_name"]})
            continue

        pantry_item = pantry_by_source.get(key)
        if pantry_item is None:
            missing.append({"food_name": item["food_name"]})
            continue

        requested = item.get("amount_multiple")
        entry = {
            "food_name": item["food_name"], "pantry_item_id": pantry_item["id"],
            "tracking_mode": pantry_item["tracking_mode"], "requested_servings": requested,
        }
        if pantry_item["tracking_mode"] == "countable" and requested is not None:
            if pantry_item["remaining_servings"] is not None and requested > pantry_item["remaining_servings"]:
                entry["sufficient"] = False
                entry["remaining_servings"] = pantry_item["remaining_servings"]
                missing.append(entry)
                continue
        have.append(entry)

    return {"have": have, "missing": missing, "unmatchable": unmatchable}


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
    pool = await get_pool()
    async with pool.acquire() as conn:
        recipe, items = await _get_recipe_with_items(conn, recipe_id, user_id)
        if recipe is None:
            raise HTTPException(status_code=404, detail="Recipe not found")
        match = await _match_recipe_against_pantry(conn, items, user_id)

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
    from .food import _recipe_per_serving_nutrition
    import json

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            recipe, items = await _get_recipe_with_items(conn, recipe_id, user_id)
            if recipe is None:
                raise HTTPException(status_code=404, detail="Recipe not found")

            match = await _match_recipe_against_pantry(conn, items, user_id)
            if match["missing"] or match["unmatchable"]:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "message": "Cannot make this recipe — missing or unmatchable ingredients",
                        **match,
                    },
                )

            decremented, removed = [], []
            for entry in match["have"]:
                pantry_item = await conn.fetchrow(
                    "SELECT * FROM pantry_items WHERE id = $1 AND user_id = $2 FOR UPDATE",
                    entry["pantry_item_id"], user_id,
                )
                if pantry_item is None:
                    continue  # already gone (e.g. duplicate recipe items referencing the same pantry row)

                if pantry_item["tracking_mode"] == "single":
                    await delete_nutrient_facts(conn, "pantry_item", pantry_item["id"])
                    await conn.execute("DELETE FROM pantry_items WHERE id = $1", pantry_item["id"])
                    removed.append(pantry_item["id"])
                elif pantry_item["tracking_mode"] == "countable" and entry["requested_servings"] is not None:
                    new_remaining = pantry_item["remaining_servings"] - entry["requested_servings"]
                    if new_remaining <= 0:
                        await delete_nutrient_facts(conn, "pantry_item", pantry_item["id"])
                        await conn.execute("DELETE FROM pantry_items WHERE id = $1", pantry_item["id"])
                        removed.append(pantry_item["id"])
                    else:
                        await conn.execute(
                            "UPDATE pantry_items SET remaining_servings = $1, updated_at = now() WHERE id = $2",
                            new_remaining, pantry_item["id"],
                        )
                        decremented.append(pantry_item["id"])
                # bulk: presence-only, never decremented -- matches
                # can-make's own bulk handling (no quantity concept).

            per_serving_macros, per_serving_nutrients = _recipe_per_serving_nutrition(recipe, items)
            pantry_item_id = await conn.fetchval(
                """INSERT INTO pantry_items (user_id, food_name, source, source_id, serving_size,
                       serving_unit, tracking_mode, remaining_servings,
                       calories, nutrients_json)
                   VALUES ($1, $2, 'recipe', $3, 1, 'serving', 'countable', $4, $5, $6)
                   RETURNING id""",
                user_id, recipe["name"], str(recipe_id), recipe["servings_per_batch"],
                per_serving_macros["calories"], json.dumps(per_serving_nutrients),
            )
            await write_nutrients(conn, "pantry_item", pantry_item_id, per_serving_nutrients)

    return {
        "status": "made",
        "pantry_item_id": pantry_item_id,
        "servings_added": recipe["servings_per_batch"],
        "ingredients_decremented": decremented,
        "ingredients_removed": removed,
    }
