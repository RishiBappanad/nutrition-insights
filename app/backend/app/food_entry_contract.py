"""
Shared food-logging and recipe-import contracts.

These Pydantic models are the canonical shape for "a food/recipe entry
to log" and "a recipe to import" across this app — any source (USDA/CNF
search results, Cronometer sync, a future MyFitnessPal importer, etc.)
converts its own data into ONE of these models, then hands it to the
corresponding service function below. Sources never write directly to
food_log/food_log_nutrients/recipes/recipe_items themselves — this is
the decoupling point: adding a new import source means writing a
converter function that produces one of these models, not touching the
storage layer or duplicating the INSERT logic that routers/food.py and
routers/recipes.py already have.

Concretely, this replaces what used to be routers/sync.py's own direct
SQL inserts into food_log/food_log_nutrients (a real, now-fixed coupling
issue — the sync path used to bypass POST /food/log's logic entirely,
meaning any future change to how a food log entry is validated/scaled/
stored would silently NOT apply to Cronometer-synced entries).
"""
import json
from typing import Optional

from pydantic import BaseModel

from .db import get_pool


class FoodLogEntryContract(BaseModel):
    """Canonical shape for one loggable food/recipe entry — identical
    field-for-field to POST /food/log's request body (see
    routers/food.py), so any source producing this model is producing
    exactly what a manual food-log call would send. `source` should
    identify the origin ("USDA", "CNF", "Cronometer", "recipe", etc.);
    `source_id` is that source's own identifier for the food, when one
    exists (Cronometer's servings export has no stable per-serving ID,
    so `source_id` is None for Cronometer-sourced entries — confirmed,
    not an oversight)."""
    date: str
    meal: str = "Snack"
    food_name: str
    source: Optional[str] = None
    source_id: Optional[str] = None
    serving_size: float = 1.0
    serving_unit: str = "serving"
    # Fiber is intentionally NOT a top-level field here — it's a
    # micronutrient, not one of the 3 true macros (protein/carbs/fat), and
    # is expected under nutrients["Fiber, total dietary"] like every other
    # nutrient (sodium, potassium, vitamins, etc.).
    calories: float = 0
    protein: float = 0
    carbs: float = 0
    fat: float = 0
    nutrients: dict = {}


class RecipeItemContract(BaseModel):
    """Canonical shape for one recipe ingredient — identical to
    RecipeItemRequest in routers/recipes.py."""
    food_name: str
    source: Optional[str] = None
    source_id: Optional[str] = None
    amount_grams: Optional[float] = None
    amount_multiple: Optional[float] = None
    calories: float = 0
    protein: float = 0
    carbs: float = 0
    fat: float = 0
    nutrients: dict = {}


class RecipeImportContract(BaseModel):
    """Canonical shape for importing a whole recipe — identical to
    RecipeRequest in routers/recipes.py. A source that has its own
    recipe concept (Cronometer, a future importer) converts to this
    model and calls import_recipe() below, rather than constructing
    recipes/recipe_items rows directly."""
    name: str
    servings_per_batch: float = 1.0
    source: Optional[str] = None
    source_id: Optional[str] = None
    items: list[RecipeItemContract] = []


class ExerciseLogContract(BaseModel):
    """Canonical shape for one loggable exercise/activity entry —
    identical field-for-field to POST /exercise's request body (see
    routers/exercise.py). Same decoupling rationale as
    FoodLogEntryContract: the manual log endpoint AND the future
    Cronometer exercise sync (once addExercise's wire format is fully
    verified — see integrations/cronometer_rpc.py) both convert into
    this one model and call log_exercise_entry() below, instead of the
    sync path doing its own SQL against exercise_log."""
    date: str
    activity_name: str
    duration_minutes: Optional[float] = None
    calories_burned: float = 0
    source: str = "manual"
    source_id: Optional[str] = None
    notes: Optional[str] = None


def _nutrients_to_rows(entity_id: int, nutrients: dict) -> list[tuple]:
    """Same normalization as routers/food.py's nutrients_to_rows — kept
    here too (not imported cross-module) since callers pass different
    target-table id columns; the validation rules are intentionally
    identical."""
    rows = []
    for name, info in nutrients.items():
        if not isinstance(info, dict):
            continue
        value = info.get("value")
        if value is None:
            continue
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        rows.append((entity_id, name, value, info.get("unit", "")))
    return rows


async def log_food_entry(user_id: int, entry: FoodLogEntryContract) -> int:
    """
    Write one FoodLogEntryContract into food_log + food_log_nutrients.

    This is the SAME insert logic POST /food/log runs (see
    routers/food.py::log_food) — any caller producing a
    FoodLogEntryContract (manual logging, Cronometer sync, a future
    importer) goes through this one function, so a future change to
    storage/validation here automatically applies to every source
    instead of needing to be duplicated per-source.

    Note: this does NOT apply portion_scaling.py's scale_to option —
    that's a request-time concern for the interactive /food/log endpoint
    (a user picking a search result and choosing an amount). Importers
    are expected to hand over already-resolved final values.

    Returns the new food_log row's id.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            food_log_id = await conn.fetchval(
                """INSERT INTO food_log (user_id, date, meal, food_name, source, source_id,
                       serving_size, serving_unit, calories, protein, carbs, fat, nutrients_json)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
                   RETURNING id""",
                user_id, entry.date, entry.meal, entry.food_name, entry.source, entry.source_id,
                entry.serving_size, entry.serving_unit, entry.calories, entry.protein,
                entry.carbs, entry.fat, json.dumps(entry.nutrients),
            )
            rows = _nutrients_to_rows(food_log_id, entry.nutrients)
            if rows:
                await conn.executemany(
                    """INSERT INTO food_log_nutrients (food_log_id, nutrient_name, value, unit)
                       VALUES ($1, $2, $3, $4)
                       ON CONFLICT (food_log_id, nutrient_name) DO UPDATE SET value = EXCLUDED.value, unit = EXCLUDED.unit""",
                    rows,
                )
    return food_log_id


async def import_recipe(user_id: int, recipe: RecipeImportContract) -> int:
    """
    Write one RecipeImportContract into recipes + recipe_items +
    recipe_item_nutrients. Supports deduplication if source and source_id
    are provided (updating existing recipe items rather than creating duplicates).

    Returns the recipe's id.
    """
    if recipe.servings_per_batch <= 0:
        raise ValueError("servings_per_batch must be positive")

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            recipe_id = None
            if recipe.source and recipe.source_id:
                recipe_id = await conn.fetchval(
                    "SELECT id FROM recipes WHERE user_id = $1 AND source = $2 AND source_id = $3",
                    user_id, recipe.source, recipe.source_id,
                )

            if recipe_id is not None:
                await conn.execute(
                    "UPDATE recipes SET name = $1, servings_per_batch = $2, updated_at = now() WHERE id = $3",
                    recipe.name, recipe.servings_per_batch, recipe_id,
                )
                await conn.execute("DELETE FROM recipe_items WHERE recipe_id = $1", recipe_id)
            else:
                recipe_id = await conn.fetchval(
                    "INSERT INTO recipes (user_id, name, servings_per_batch, source, source_id) VALUES ($1, $2, $3, $4, $5) RETURNING id",
                    user_id, recipe.name, recipe.servings_per_batch, recipe.source, recipe.source_id,
                )

            for item in recipe.items:
                item_id = await conn.fetchval(
                    """INSERT INTO recipe_items (recipe_id, food_name, source, source_id, amount_grams, amount_multiple,
                           calories, protein, carbs, fat, nutrients_json)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                       RETURNING id""",
                    recipe_id, item.food_name, item.source, item.source_id, item.amount_grams, item.amount_multiple,
                    item.calories, item.protein, item.carbs, item.fat, json.dumps(item.nutrients),
                )
                rows = _nutrients_to_rows(item_id, item.nutrients)
                if rows:
                    await conn.executemany(
                        "INSERT INTO recipe_item_nutrients (recipe_item_id, nutrient_name, value, unit) VALUES ($1, $2, $3, $4)",
                        rows,
                    )
    return recipe_id


async def log_exercise_entry(user_id: int, entry: ExerciseLogContract) -> tuple[int, bool]:
    """
    Write one ExerciseLogContract into exercise_log — the same insert
    logic POST /exercise runs (see routers/exercise.py::log_exercise).
    The manual log endpoint and the future Cronometer exercise sync both
    call this, so a future change to storage/validation here applies to
    both sources automatically instead of needing duplication.

    Unlike log_food_entry, this does a dedupe check on (user_id, source,
    source_id) when source_id is present — Cronometer sync entries carry
    a deterministic composite source_id (see routers/sync.py's
    _exercise_row_source_id), so re-syncing the same day doesn't create
    duplicate entries the way the food diary sync did before its
    sync-pointer work (a known, deliberately-not-yet-fixed gap there —
    this avoids repeating that mistake for exercise from the start).
    Manual entries (source='manual', source_id=None) never dedupe
    against each other — a user can log "Running" twice in one day on
    purpose.

    Returns (id, was_created) — was_created is False when an existing
    row was deduped against rather than a new one inserted. Callers that
    only care about the id (e.g. POST /exercise, which never sends a
    source_id) can ignore the second element; callers that need to
    distinguish "newly imported" from "already had this" for a count
    (e.g. the Cronometer sync path) rely on it explicitly — a real bug
    this exact distinction was needed to catch: a naive
    "count += 1 regardless" in the sync path would silently over-report
    how many entries a resync actually imported, since the DB-level
    dedupe was correct but nothing surfaced that fact to the caller.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        if entry.source_id is not None:
            existing_id = await conn.fetchval(
                "SELECT id FROM exercise_log WHERE user_id = $1 AND source = $2 AND source_id = $3",
                user_id, entry.source, entry.source_id,
            )
            if existing_id is not None:
                return existing_id, False

        entry_id = await conn.fetchval(
            """INSERT INTO exercise_log (user_id, date, activity_name, duration_minutes, calories_burned, source, source_id, notes)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
               RETURNING id""",
            user_id, entry.date, entry.activity_name, entry.duration_minutes, entry.calories_burned,
            entry.source, entry.source_id, entry.notes,
        )
    return entry_id, True
