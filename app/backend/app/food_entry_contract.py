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
    calories: float = 0
    protein: float = 0
    carbs: float = 0
    fat: float = 0
    fiber: float = 0
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
    fiber: float = 0
    nutrients: dict = {}


class RecipeImportContract(BaseModel):
    """Canonical shape for importing a whole recipe — identical to
    RecipeRequest in routers/recipes.py. A source that has its own
    recipe concept (Cronometer, a future importer) converts to this
    model and calls import_recipe() below, rather than constructing
    recipes/recipe_items rows directly."""
    name: str
    servings_per_batch: float = 1.0
    items: list[RecipeItemContract] = []


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
                       serving_size, serving_unit, calories, protein, carbs, fat, fiber, nutrients_json)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
                   RETURNING id""",
                user_id, entry.date, entry.meal, entry.food_name, entry.source, entry.source_id,
                entry.serving_size, entry.serving_unit, entry.calories, entry.protein,
                entry.carbs, entry.fat, entry.fiber, json.dumps(entry.nutrients),
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
    recipe_item_nutrients — the same insert logic POST /recipes runs
    (see routers/recipes.py::create_recipe and its _save_items helper).

    A source with its own recipe concept (a future Cronometer recipe
    importer, once addFood's field-order schema is actually decoded —
    see nutrition-diary-design.md for why that's deferred, not built
    blind) converts to this model and calls this function, rather than
    writing recipes/recipe_items rows directly.

    Returns the new recipe's id.
    """
    if recipe.servings_per_batch <= 0:
        raise ValueError("servings_per_batch must be positive")

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            recipe_id = await conn.fetchval(
                "INSERT INTO recipes (user_id, name, servings_per_batch) VALUES ($1, $2, $3) RETURNING id",
                user_id, recipe.name, recipe.servings_per_batch,
            )
            for item in recipe.items:
                item_id = await conn.fetchval(
                    """INSERT INTO recipe_items (recipe_id, food_name, source, source_id, amount_grams, amount_multiple,
                           calories, protein, carbs, fat, fiber, nutrients_json)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                       RETURNING id""",
                    recipe_id, item.food_name, item.source, item.source_id, item.amount_grams, item.amount_multiple,
                    item.calories, item.protein, item.carbs, item.fat, item.fiber, json.dumps(item.nutrients),
                )
                rows = _nutrients_to_rows(item_id, item.nutrients)
                if rows:
                    await conn.executemany(
                        "INSERT INTO recipe_item_nutrients (recipe_item_id, nutrient_name, value, unit) VALUES ($1, $2, $3, $4)",
                        rows,
                    )
    return recipe_id
