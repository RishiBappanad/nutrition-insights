"""Food search and logging API routes."""
from fastapi import APIRouter, Depends, HTTPException, Query
from ..routers.auth import get_current_user
from ..db import get_pool
from ..portion_scaling import scale_food_entry
from ..food_entry_contract import FoodLogEntryContract, log_food_entry, _nutrients_to_rows as nutrients_to_rows

router = APIRouter()


@router.get("/search")
async def search_food(
    q: str = Query(..., min_length=2),
    sources: str = Query("USDA,CNF"),
    include_own: bool = Query(True),
    user_id: int = Depends(get_current_user),
):
    """
    Search foods across USDA and CNF databases, AND (by default) the
    user's own saved recipes AND meals — matching Cronometer's own model,
    where a recipe/meal is just another searchable, loggable food-like
    entity (see nutrition-diary-design.md: Cronometer's real /food-search
    response includes plain foods and recipes together, distinguished by
    `recipe`/`meal` booleans, not separate searches). Per explicit user
    steering: both recipes AND meals should be valid food references at
    every point a plain food is — searchable, loggable to diary, and
    addable to pantry — without any caller needing special-case logic.

    A recipe result has source="recipe", id=<the recipe's own id>.
    A meal result has source="meal", id=<the meal's own id>. Both use
    the same (source, source_id) pair already used everywhere else a
    food reference is stored (food_log, pantry_items, recipe_items,
    meal_items) — but they resolve differently when actually LOGGED (see
    routers/pantry.py's consume flow and the frontend's food-log.jsx):
    a recipe logs as ONE aggregated food_log entry (scaled by servings),
    a meal logs as MULTIPLE food_log entries (one per item, at face
    value, matching POST /meals/{id}/log's existing behavior) — a caller
    that only ever calls POST /food/log directly (bypassing the
    recipe/meal-specific log endpoints) will NOT get a meal's per-item
    breakdown; it should instead call POST /meals/{id}/log for a
    source="meal" result, the same way the frontend does.

    `include_own=false` restricts to only the external sources (e.g. if
    a caller specifically wants to exclude the user's own recipes/meals,
    though no current caller does this).
    """
    from integrations.food_search import search_foods

    source_list = [s.strip() for s in sources.split(",")]
    results = search_foods(q, source_list)

    if include_own:
        results.extend(await _search_user_recipes(user_id, q))
        results.extend(await _search_user_meals(user_id, q))

    return {"results": results}


async def _search_user_recipes(user_id: int, query: str) -> list[dict]:
    """Match the user's own recipes by name (case-insensitive substring)
    and shape each as a food-search result — per-serving totals (not
    batch totals), since logging a recipe from search should default to
    "1 serving," matching how a plain food search result represents one
    reference unit."""
    from .recipes import _get_recipe_with_items

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, name, servings_per_batch FROM recipes WHERE user_id = $1 AND name ILIKE $2 ORDER BY name",
            user_id, f"%{query}%",
        )

    results = []
    for row in rows:
        pool = await get_pool()
        async with pool.acquire() as conn:
            recipe, items = await _get_recipe_with_items(conn, row["id"], user_id)
        per_serving_macros, per_serving_nutrients = _recipe_per_serving_nutrition(recipe, items)
        results.append({
            "source": "recipe",
            "id": str(recipe["id"]),
            "name": recipe["name"],
            "brand": "",
            "category": "Recipe",
            "nutrients": {
                # per_serving_nutrients already carries "Fiber, total
                # dietary" (scaled like any other nutrient) — only the 4
                # true macros need to be overlaid in the standard USDA
                # nutrient-map shape below, so downstream extractMacro()-
                # style helpers on the frontend work identically for a
                # recipe result as for a plain food result.
                **per_serving_nutrients,
                "Energy": {"value": round(per_serving_macros["calories"]), "unit": "KCAL"},
                "Protein": {"value": round(per_serving_macros["protein"], 2), "unit": "G"},
                "Carbohydrate, by difference": {"value": round(per_serving_macros["carbs"], 2), "unit": "G"},
                "Total lipid (fat)": {"value": round(per_serving_macros["fat"], 2), "unit": "G"},
            },
            "serving_size": 1,
            "serving_unit": "serving",
            "recipe": True,
            "meal": False,
            "recipeOrMeal": True,
        })
    return results


def _recipe_per_serving_nutrition(recipe, items) -> tuple[dict, dict]:
    """Batch totals (from recipe items) divided down to ONE serving —
    shared by _search_user_recipes (a recipe search result shows
    per-serving totals) and routers/recipes.py's make_recipe (the
    resulting pantry item is added at 1-serving-per-unit, same
    convention). Isolated here so both call sites can't silently drift
    apart on how "per serving" is computed. Returns (macros, nutrients)."""
    from .recipes import _aggregate_batch_totals
    from ..portion_scaling import scale_macros, scale_nutrients

    batch = _aggregate_batch_totals(items)
    factor = 1.0 / recipe["servings_per_batch"]
    return scale_macros(batch["macros"], factor), scale_nutrients(batch["nutrients"], factor)


async def _search_user_meals(user_id: int, query: str) -> list[dict]:
    """Match the user's own meals by name and shape each as a food-search
    result — SUMMED item totals (no batch division, since meals have no
    servings-per-batch concept per routers/meals.py's design: a meal
    always logs at face value). The `meal: True` flag on the result
    signals to callers that logging this result means calling
    POST /meals/{id}/log (multiple food_log rows, one per item) rather
    than POST /food/log directly (which would create a single row with
    no per-item breakdown) — see search_food()'s docstring."""
    from .meals import _get_meal_with_items

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, name FROM meals WHERE user_id = $1 AND name ILIKE $2 ORDER BY name",
            user_id, f"%{query}%",
        )

    results = []
    for row in rows:
        pool = await get_pool()
        async with pool.acquire() as conn:
            meal, items = await _get_meal_with_items(conn, row["id"], user_id)

        macros = {"calories": 0.0, "protein": 0.0, "carbs": 0.0, "fat": 0.0}
        nutrients: dict = {}
        for item in items:
            for k in macros:
                macros[k] += item.get(k, 0) or 0
            # Fiber ("Fiber, total dietary") is summed here along with
            # every other non-macro nutrient — it is not a separate macro
            # field on a meal item.
            for name, info in item.get("nutrients", {}).items():
                bucket = nutrients.setdefault(name, {"value": 0.0, "unit": info["unit"]})
                bucket["value"] += info["value"]

        results.append({
            "source": "meal",
            "id": str(meal["id"]),
            "name": meal["name"],
            "brand": "",
            "category": "Meal",
            "nutrients": {
                **nutrients,
                "Energy": {"value": round(macros["calories"]), "unit": "KCAL"},
                "Protein": {"value": round(macros["protein"], 2), "unit": "G"},
                "Carbohydrate, by difference": {"value": round(macros["carbs"], 2), "unit": "G"},
                "Total lipid (fat)": {"value": round(macros["fat"], 2), "unit": "G"},
            },
            "serving_size": 1,
            "serving_unit": "meal",
            "recipe": False,
            "meal": True,
            "recipeOrMeal": True,
            "item_count": len(items),
        })
    return results


@router.post("/log")
async def log_food(
    entry: dict,
    user_id: int = Depends(get_current_user),
):
    """
    Log a food entry. Body:
    {
        "date": "2026-06-22",
        "meal": "Lunch",
        "food_name": "Bananas, raw",
        "source": "USDA",
        "source_id": "173944",
        "serving_size": 1.0,
        "serving_unit": "medium (118g)",
        "calories": 105,
        "protein": 1.3,
        "carbs": 27,
        "fat": 0.4,
        "nutrients": {"Fiber, total dietary": {"value": 3.1, "unit": "G"}, "Sodium, Na": {"value": 1.2, "unit": "mg"}, ...},
        "scale_to": {"mode": "grams", "from_grams": 118, "to_grams": 250}
    }

    Fiber is NOT a top-level field — it's a micronutrient, not one of the
    3 true macros (protein/carbs/fat), so it belongs in `nutrients` under
    "Fiber, total dietary" like every other nutrient. A legacy top-level
    `fiber` value is still accepted for one release for backwards
    compatibility with older frontend builds — it's folded into
    `nutrients["Fiber, total dietary"]` if that key isn't already present.

    `nutrients` (if present) is persisted structurally into
    food_log_nutrients (one row per nutrient), not just kept as an
    unread JSON blob — this is what lets /nutrition/progress compute
    per-nutrient daily totals with SQL instead of parsing JSON per row.
    Entries without a `calories`/`protein`/etc. key in `nutrients` still
    get those 4 macro columns populated from the top-level fields for
    backwards compatibility with the existing dashboard totals.

    `scale_to` is optional. If present, the backend scales `calories`/
    `protein`/`carbs`/`fat`/`nutrients` (fiber scales as part of
    `nutrients`) by the requested amount before storing — the caller
    sends the food's reference (unscaled) values plus the target amount,
    not pre-scaled numbers, so the actual multiplication happens in one
    place (portion_scaling.py) instead of being reimplemented by every
    caller (or, before this existed, not implemented at all). If
    `scale_to` is omitted, the request body's top-level fields are stored
    exactly as given — unchanged behavior for existing callers.
      - mode="grams": {"from_grams": 118, "to_grams": 250} — for foods
        with a real gram-based reference (USDA/CNF's serving_size, when
        it's a weight).
      - mode="multiple": {"servings_requested": 2} — for foods with no
        gram reference (e.g. "1 jar"), just N of the reference serving.

    The actual storage write goes through food_entry_contract.log_food_entry()
    — the same shared function any import source (Cronometer sync, a
    future importer) uses, so this endpoint and every sync path stay
    behaviorally identical rather than maintaining two copies of the
    insert logic.
    """
    nutrients: dict = dict(entry.get("nutrients") or {})
    legacy_fiber = entry.get("fiber")
    if legacy_fiber is not None and "Fiber, total dietary" not in nutrients:
        nutrients["Fiber, total dietary"] = {"value": legacy_fiber, "unit": "G"}

    macros = {
        "calories": entry.get("calories", 0),
        "protein": entry.get("protein", 0),
        "carbs": entry.get("carbs", 0),
        "fat": entry.get("fat", 0),
    }

    scale_to = entry.get("scale_to")
    if scale_to:
        try:
            scaled = scale_food_entry(
                macros=macros,
                nutrients=nutrients,
                mode=scale_to.get("mode"),
                from_grams=scale_to.get("from_grams"),
                to_grams=scale_to.get("to_grams"),
                servings_requested=scale_to.get("servings_requested"),
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        macros = scaled["macros"]
        nutrients = scaled["nutrients"]

    contract_entry = FoodLogEntryContract(
        date=entry.get("date"),
        meal=entry.get("meal", "Snack"),
        food_name=entry.get("food_name"),
        source=entry.get("source"),
        source_id=entry.get("source_id"),
        serving_size=entry.get("serving_size", 1.0),
        serving_unit=entry.get("serving_unit", "serving"),
        calories=macros["calories"],
        protein=macros["protein"],
        carbs=macros["carbs"],
        fat=macros["fat"],
        nutrients=nutrients,
    )
    food_log_id = await log_food_entry(user_id, contract_entry)
    return {"status": "logged", "id": food_log_id}


@router.get("/log")
async def get_food_log(
    date: str = Query(...),
    user_id: int = Depends(get_current_user),
):
    """Get all food entries for a given date, including each entry's full
    per-nutrient breakdown (from food_log_nutrients) and day-level totals
    for every nutrient that appears on at least one entry — not just the
    4 hardcoded macro columns. Fiber ("Fiber, total dietary") is one of
    the entries in `nutrients`/`nutrient_totals`, not a 5th macro column —
    there is no dedicated `fiber` field in `entries`/`totals` below."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM food_log WHERE user_id = $1 AND date = $2 ORDER BY id",
            user_id, date,
        )
        entry_ids = [r["id"] for r in rows]
        nutrient_rows = []
        if entry_ids:
            nutrient_rows = await conn.fetch(
                "SELECT food_log_id, nutrient_name, value, unit FROM food_log_nutrients "
                "WHERE food_log_id = ANY($1::int[])",
                entry_ids,
            )

    nutrients_by_entry: dict[int, dict] = {}
    for nr in nutrient_rows:
        nutrients_by_entry.setdefault(nr["food_log_id"], {})[nr["nutrient_name"]] = {
            "value": nr["value"],
            "unit": nr["unit"],
        }

    entries = []
    nutrient_totals: dict[str, dict] = {}
    for r in rows:
        entry_nutrients = nutrients_by_entry.get(r["id"], {})
        entries.append({
            "id": r["id"],
            "date": r["date"],
            "meal": r["meal"],
            "food_name": r["food_name"],
            "source": r["source"],
            "serving_size": r["serving_size"],
            "serving_unit": r["serving_unit"],
            "calories": r["calories"],
            "protein": r["protein"],
            "carbs": r["carbs"],
            "fat": r["fat"],
            "nutrients": entry_nutrients,
        })
        for name, info in entry_nutrients.items():
            bucket = nutrient_totals.setdefault(name, {"value": 0.0, "unit": info["unit"]})
            bucket["value"] += info["value"]

    totals = {
        "calories": sum(e["calories"] for e in entries),
        "protein": sum(e["protein"] for e in entries),
        "carbs": sum(e["carbs"] for e in entries),
        "fat": sum(e["fat"] for e in entries),
    }

    return {"entries": entries, "totals": totals, "nutrient_totals": nutrient_totals}


@router.delete("/log/{entry_id}")
async def delete_food_entry(
    entry_id: int,
    user_id: int = Depends(get_current_user),
):
    """Delete a food log entry. Scoped to the current user so one user cannot
    delete another user's entry by guessing an id."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM food_log WHERE id = $1 AND user_id = $2", entry_id, user_id
        )
    return {"status": "deleted"}
