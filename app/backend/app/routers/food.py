"""Food search and logging API routes."""
from fastapi import APIRouter, Depends, HTTPException, Query
from ..routers.auth import get_current_user
from ..db import get_pool
from ..db.food import query as food_query
from ..portion_scaling import scale_food_entry
from ..food_entry_contract import FoodLogEntryContract, log_food_entry
from ..nutrient_groups import order_nutrients

router = APIRouter()


VALID_SEARCH_SOURCES = {"USDA", "CNF", "recipe", "meal", "pantry"}
# "recipe" and "meal" default on to preserve this endpoint's original
# behavior (own recipes/meals always included) for any caller not passing
# `sources` explicitly. "pantry" defaults off -- it was never part of the
# result set before this became a selectable source (added 2026-09-10 per
# user request), so an unfiltered caller shouldn't suddenly see pantry
# items mixed into results they weren't there before.
DEFAULT_SEARCH_SOURCES = "USDA,CNF,recipe,meal"


@router.get("/search")
async def search_food(
    q: str = Query(..., min_length=2),
    sources: str = Query(DEFAULT_SEARCH_SOURCES),
    user_id: int = Depends(get_current_user),
):
    """
    Search across USDA, CNF, the user's own saved recipes and meals, and
    (opt-in) the user's own pantry — matching Cronometer's own model,
    where a recipe/meal is just another searchable, loggable food-like
    entity (see nutrition-diary-design.md: Cronometer's real /food-search
    response includes plain foods and recipes together, distinguished by
    `recipe`/`meal` booleans, not separate searches). Per explicit user
    steering: both recipes AND meals should be valid food references at
    every point a plain food is — searchable, loggable to diary, and
    addable to pantry — without any caller needing special-case logic.

    A recipe result has source="recipe", id=<the recipe's own id>. A meal
    result has source="meal", id=<the meal's own id>. A pantry result has
    source="pantry", id=<the pantry item's own id>. All three use the
    same (source, source_id) pair already used everywhere else a food
    reference is stored (food_log, pantry_items, recipe_items,
    meal_items) — but recipe/meal resolve differently when actually
    LOGGED (see routers/pantry.py's consume flow and the frontend's
    food-log.jsx): a recipe logs as ONE aggregated food_log entry (scaled
    by servings), a meal logs as MULTIPLE food_log entries (one per item,
    at face value, matching POST /meals/{id}/log's existing behavior) —
    a caller that only ever calls POST /food/log directly (bypassing the
    recipe/meal-specific log endpoints) will NOT get a meal's per-item
    breakdown; it should instead call POST /meals/{id}/log for a
    source="meal" result, the same way the frontend does. A pantry
    result logs like a plain food (POST /food/log directly).

    `sources` is a comma-separated subset of {"USDA", "CNF", "recipe",
    "meal", "pantry"} -- any combination, in any order. Defaults to
    "USDA,CNF,recipe,meal" (this endpoint's original always-included set,
    preserved for backward compatibility; "pantry" is opt-in only, see
    DEFAULT_SEARCH_SOURCES above). An unrecognized value is silently
    ignored rather than rejected, matching how an empty/all-excluded
    `sources` already degrades to zero results rather than an error.
    """
    from integrations.food_search import search_foods

    source_list = [s.strip() for s in sources.split(",")]
    results = search_foods(q, source_list)

    if "recipe" in source_list:
        results.extend(await _search_user_recipes(user_id, q))
    if "meal" in source_list:
        results.extend(await _search_user_meals(user_id, q))
    if "pantry" in source_list:
        results.extend(await _search_user_pantry(user_id, q))

    for r in results:
        if "nutrients" in r:
            r["nutrients"] = order_nutrients(r["nutrients"])

    return {"results": results}


async def _search_user_recipes(user_id: int, query: str) -> list[dict]:
    """Match the user's own recipes by name (case-insensitive substring)
    and shape each as a food-search result — per-serving totals (not
    batch totals), since logging a recipe from search should default to
    "1 serving," matching how a plain food search result represents one
    reference unit."""
    from .recipes import _get_recipe_with_items

    rows = await food_query.search_recipes_by_name(user_id, query)

    # One connection reused for every matched recipe, not re-acquired per
    # row -- a real N+1 (one pool.acquire() round-trip per search result)
    # of the same shape as the Cronometer diary-sync bug fixed elsewhere
    # in this codebase, just smaller in practice since a search typically
    # matches a handful of the user's own recipes, not thousands of rows.
    results = []
    pool = await get_pool()
    async with pool.acquire() as conn:
        for row in rows:
            recipe, items = await _get_recipe_with_items(conn, row["id"], user_id)
            per_serving_macros, per_serving_nutrients = _recipe_per_serving_nutrition(recipe, items)
            results.append({
                "source": "recipe",
                "id": str(recipe["id"]),
                "name": recipe["name"],
                "brand": "",
                # This used to be the hardcoded string "Recipe" -- a
                # UI-facing "what kind of result is this" label that
                # collided with the real food-type category field added
                # 2026-09-08 (app/food_category.py). The `recipe`/`meal`/
                # `recipeOrMeal` booleans below already cover "what kind of
                # result," and confirmed nothing in the frontend reads
                # `.category`'s string value, so this now carries the
                # recipe's actual resolved category instead.
                "category": recipe["category"],
                "nutrients": {
                    # per_serving_nutrients already carries "Protein",
                    # "Carbohydrate, by difference", "Total lipid (fat)", and
                    # "Fiber, total dietary" (scaled like any other nutrient,
                    # since none of them are top-level macro fields on a
                    # recipe item anymore) — only Energy (calories) needs to
                    # be overlaid, since that's the one field with its own
                    # dedicated top-level column.
                    **per_serving_nutrients,
                    "Energy": {"value": round(per_serving_macros["calories"]), "unit": "KCAL"},
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

    rows = await food_query.search_meals_by_name(user_id, query)

    # One connection reused for every matched meal, not re-acquired per
    # row -- see _search_user_recipes's matching comment above; same
    # N+1-connection anti-pattern, same fix.
    results = []
    pool = await get_pool()
    async with pool.acquire() as conn:
        for row in rows:
            meal, items = await _get_meal_with_items(conn, row["id"], user_id)

            macros = {"calories": 0.0}
            nutrients: dict = {}
            for item in items:
                macros["calories"] += item.get("calories", 0) or 0
                # Protein/carbs/fat/fiber are summed here along with every
                # other non-macro nutrient — calories is the only field with
                # its own dedicated column on a meal item.
                for name, info in item.get("nutrients", {}).items():
                    bucket = nutrients.setdefault(name, {"value": 0.0, "unit": info["unit"]})
                    bucket["value"] += info["value"]

            results.append({
                "source": "meal",
                "id": str(meal["id"]),
                "name": meal["name"],
                "brand": "",
                "category": meal["category"],
                "nutrients": {
                    **nutrients,
                    "Energy": {"value": round(macros["calories"]), "unit": "KCAL"},
                },
                "serving_size": 1,
                "serving_unit": "meal",
                "recipe": False,
                "meal": True,
                "recipeOrMeal": True,
                "item_count": len(items),
            })
    return results


async def _search_user_pantry(user_id: int, query: str) -> list[dict]:
    """Match the user's own (unfinished) pantry items by name and shape
    each as a food-search result. Unlike a recipe/meal result, a pantry
    result logs like a plain food (no aggregation/per-item breakdown
    involved) -- POST /food/log directly, same as any USDA/CNF result.
    No N+1 connection concern here: search_pantry_by_name already reads
    the matched rows and their nutrients together in one acquired
    connection, unlike recipes/meals which each need a further per-row
    fetch (their own items) that the caller (this function) has to loop."""
    rows, nutrients_by_item = await food_query.search_pantry_by_name(user_id, query)

    results = []
    for row in rows:
        results.append({
            "source": "pantry",
            "id": str(row["id"]),
            "name": row["food_name"],
            "brand": "",
            "category": row["category"],
            "nutrients": {
                **nutrients_by_item.get(row["id"], {}),
                "Energy": {"value": round(row["calories"] or 0), "unit": "KCAL"},
            },
            "serving_size": row["serving_size"],
            "serving_unit": row["serving_unit"],
            "recipe": False,
            "meal": False,
            "recipeOrMeal": False,
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
        "nutrients": {
            "Protein": {"value": 1.3, "unit": "G"},
            "Carbohydrate, by difference": {"value": 27, "unit": "G"},
            "Total lipid (fat)": {"value": 0.4, "unit": "G"},
            "Fiber, total dietary": {"value": 3.1, "unit": "G"},
            "Sodium, Na": {"value": 1.2, "unit": "mg"}, ...
        },
        "scale_to": {"mode": "grams", "from_grams": 118, "to_grams": 250}
    }

    `calories` is the sole top-level numeric field (TrackStack's "amount"
    for this tracker, per the Event Contract). Protein/carbs/fat/fiber
    are NOT top-level fields — they're micronutrients-in-spirit here (no
    field is "more of a macro" than another except calories), so they
    belong in `nutrients` under their standard USDA names ("Protein",
    "Carbohydrate, by difference", "Total lipid (fat)", "Fiber, total
    dietary") like every other nutrient. Legacy top-level `protein`/
    `carbs`/`fat`/`fiber` values are still accepted for one release for
    backwards compatibility with older frontend builds — each is folded
    into its corresponding `nutrients` key if that key isn't already
    present.

    `nutrients` (if present) is persisted structurally into
    food_log_nutrients (one row per nutrient), not just kept as an
    unread JSON blob — this is what lets /nutrition/progress compute
    per-nutrient daily totals with SQL instead of parsing JSON per row.

    `scale_to` is optional. If present, the backend scales `calories`/
    `nutrients` (protein/carbs/fat/fiber scale as part of `nutrients`) by
    the requested amount before storing — the caller sends the food's
    reference (unscaled) values plus the target amount, not pre-scaled
    numbers, so the actual multiplication happens in one place
    (portion_scaling.py) instead of being reimplemented by every caller
    (or, before this existed, not implemented at all). If `scale_to` is
    omitted, the request body's top-level fields are stored exactly as
    given — unchanged behavior for existing callers.
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
    _LEGACY_MACRO_KEYS = {
        "protein": ("Protein", "G"),
        "carbs": ("Carbohydrate, by difference", "G"),
        "fat": ("Total lipid (fat)", "G"),
        "fiber": ("Fiber, total dietary", "G"),
    }
    for legacy_key, (nutrient_name, unit) in _LEGACY_MACRO_KEYS.items():
        legacy_value = entry.get(legacy_key)
        if legacy_value is not None and nutrient_name not in nutrients:
            nutrients[nutrient_name] = {"value": legacy_value, "unit": unit}

    macros = {
        "calories": entry.get("calories", 0),
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
        # Frontend passes through whatever category the search result it
        # was picked from already had resolved (see integrations/
        # food_search.py) -- this endpoint doesn't re-derive it.
        category=entry.get("category"),
        serving_size=entry.get("serving_size", 1.0),
        serving_unit=entry.get("serving_unit", "serving"),
        calories=macros["calories"],
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
    per-nutrient breakdown (from nutrient_facts, owner_type='food_log') and
    day-level totals for every nutrient that appears on at least one
    entry. `calories` is the only macro-like field with its own dedicated
    column — protein, carbs, fat, and fiber are entries in
    `nutrients`/`nutrient_totals` (under "Protein", "Carbohydrate, by
    difference", "Total lipid (fat)", "Fiber, total dietary") like every
    other nutrient, not their own fields in `entries`/`totals` below."""
    rows, nutrients_by_entry = await food_query.list_food_log(user_id, date)

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
            "category": r["category"],
            "serving_size": r["serving_size"],
            "serving_unit": r["serving_unit"],
            "calories": r["calories"],
            "nutrients": entry_nutrients,
        })
        for name, info in entry_nutrients.items():
            bucket = nutrient_totals.setdefault(name, {"value": 0.0, "unit": info["unit"]})
            bucket["value"] += info["value"]

    totals = {
        "calories": sum(e["calories"] for e in entries),
    }

    return {"entries": entries, "totals": totals, "nutrient_totals": order_nutrients(nutrient_totals)}


@router.delete("/log/{entry_id}")
async def delete_food_entry(
    entry_id: int,
    user_id: int = Depends(get_current_user),
):
    """Delete a food log entry. Scoped to the current user so one user cannot
    delete another user's entry by guessing an id. nutrient_facts has no FK
    to cascade automatically (see app/nutrient_facts.py), so its rows for
    this entry are deleted explicitly first."""
    await food_query.delete_food_entry(entry_id, user_id)
    return {"status": "deleted"}
