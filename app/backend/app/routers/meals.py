"""Custom meals API: a simple named collection of items, logged together
at face value. Deliberately simpler than recipes (routers/recipes.py) --
no servings-per-batch, no scaling, no pantry availability check. A meal
is just "these items, saved together, logged all at once" (e.g. "My usual
breakfast" = eggs + toast + coffee, always logged as that exact trio)."""
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..routers.auth import get_current_user
from ..db import get_pool

router = APIRouter()


class MealItemRequest(BaseModel):
    food_name: str
    source: Optional[str] = None
    source_id: Optional[str] = None
    serving_size: float = 1.0
    serving_unit: str = "serving"
    # `calories` is the sole top-level numeric field. Protein/carbs/fat/
    # fiber belong in nutrients under their standard USDA names.
    calories: float = 0
    nutrients: dict = {}


class MealRequest(BaseModel):
    name: str
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


def _item_nutrient_rows(item_id: int, nutrients: dict) -> list[tuple]:
    rows = []
    for name, info in nutrients.items():
        if not isinstance(info, dict) or info.get("value") is None:
            continue
        try:
            value = float(info["value"])
        except (TypeError, ValueError):
            continue
        rows.append((item_id, name, value, info.get("unit", "")))
    return rows


async def _save_items(conn, meal_id: int, items: list[MealItemRequest]):
    for item in items:
        item_id = await conn.fetchval(
            """INSERT INTO meal_items (meal_id, food_name, source, source_id, serving_size, serving_unit,
                   calories, nutrients_json)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
               RETURNING id""",
            meal_id, item.food_name, item.source, item.source_id, item.serving_size, item.serving_unit,
            item.calories, json.dumps(item.nutrients),
        )
        rows = _item_nutrient_rows(item_id, item.nutrients)
        if rows:
            await conn.executemany(
                "INSERT INTO meal_item_nutrients (meal_item_id, nutrient_name, value, unit) VALUES ($1, $2, $3, $4)",
                rows,
            )


@router.post("")
async def create_meal(req: MealRequest, user_id: int = Depends(get_current_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            meal_id = await conn.fetchval(
                "INSERT INTO meals (user_id, name) VALUES ($1, $2) RETURNING id", user_id, req.name,
            )
            await _save_items(conn, meal_id, req.items)
    return {"status": "created", "id": meal_id}


@router.get("")
async def list_meals(user_id: int = Depends(get_current_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, name FROM meals WHERE user_id = $1 ORDER BY name", user_id)
    return {"meals": [{"id": r["id"], "name": r["name"]} for r in rows]}


async def _get_meal_with_items(conn, meal_id: int, user_id: int):
    meal = await conn.fetchrow("SELECT * FROM meals WHERE id = $1 AND user_id = $2", meal_id, user_id)
    if meal is None:
        return None, []
    item_rows = await conn.fetch("SELECT * FROM meal_items WHERE meal_id = $1 ORDER BY id", meal_id)
    item_ids = [r["id"] for r in item_rows]
    nutrient_rows = []
    if item_ids:
        nutrient_rows = await conn.fetch(
            "SELECT meal_item_id, nutrient_name, value, unit FROM meal_item_nutrients WHERE meal_item_id = ANY($1::int[])",
            item_ids,
        )
    nutrients_by_item: dict[int, dict] = {}
    for nr in nutrient_rows:
        nutrients_by_item.setdefault(nr["meal_item_id"], {})[nr["nutrient_name"]] = {
            "value": nr["value"], "unit": nr["unit"],
        }
    items = []
    for r in item_rows:
        items.append({
            "id": r["id"],
            "food_name": r["food_name"],
            "source": r["source"],
            "source_id": r["source_id"],
            "serving_size": r["serving_size"],
            "serving_unit": r["serving_unit"],
            "calories": r["calories"],
            "nutrients": nutrients_by_item.get(r["id"], {}),
        })
    return meal, items


@router.get("/{meal_id}")
async def get_meal(meal_id: int, user_id: int = Depends(get_current_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        meal, items = await _get_meal_with_items(conn, meal_id, user_id)
    if meal is None:
        raise HTTPException(status_code=404, detail="Meal not found")
    return {"id": meal["id"], "name": meal["name"], "items": items}


@router.put("/{meal_id}")
async def update_meal(meal_id: int, req: MealRequest, user_id: int = Depends(get_current_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            existing = await conn.fetchrow("SELECT id FROM meals WHERE id = $1 AND user_id = $2", meal_id, user_id)
            if existing is None:
                raise HTTPException(status_code=404, detail="Meal not found")
            await conn.execute("UPDATE meals SET name = $1, updated_at = now() WHERE id = $2", req.name, meal_id)
            await conn.execute("DELETE FROM meal_items WHERE meal_id = $1", meal_id)
            await _save_items(conn, meal_id, req.items)
    return {"status": "updated"}


@router.delete("/{meal_id}")
async def delete_meal(meal_id: int, user_id: int = Depends(get_current_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM meals WHERE id = $1 AND user_id = $2", meal_id, user_id)
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
    pool = await get_pool()
    async with pool.acquire() as conn:
        meal, items = await _get_meal_with_items(conn, meal_id, user_id)
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

            async with conn.transaction():
                food_log_id = await conn.fetchval(
                    """INSERT INTO food_log (user_id, date, meal, food_name, source, source_id,
                           serving_size, serving_unit, calories, nutrients_json)
                       VALUES ($1, $2, $3, $4, 'meal', $5, 1, 'meal', $6, $7)
                       RETURNING id""",
                    user_id, req.date, req.meal, meal["name"], str(meal_id),
                    macros["calories"], json.dumps(nutrients),
                )
                nutrient_rows = [(food_log_id, name, info["value"], info["unit"]) for name, info in nutrients.items()]
                if nutrient_rows:
                    await conn.executemany(
                        "INSERT INTO food_log_nutrients (food_log_id, nutrient_name, value, unit) VALUES ($1, $2, $3, $4)",
                        nutrient_rows,
                    )
            return {"status": "logged", "food_log_ids": [food_log_id], "combined": True}

        food_log_ids = []
        async with conn.transaction():
            for item in items:
                food_log_id = await conn.fetchval(
                    """INSERT INTO food_log (user_id, date, meal, food_name, source, source_id,
                           serving_size, serving_unit, calories, nutrients_json)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                       RETURNING id""",
                    user_id, req.date, req.meal, item["food_name"], item["source"], item["source_id"],
                    item["serving_size"], item["serving_unit"], item["calories"], json.dumps(item["nutrients"]),
                )
                food_log_ids.append(food_log_id)
                nutrient_rows = [
                    (food_log_id, name, info["value"], info["unit"]) for name, info in item["nutrients"].items()
                ]
                if nutrient_rows:
                    await conn.executemany(
                        "INSERT INTO food_log_nutrients (food_log_id, nutrient_name, value, unit) VALUES ($1, $2, $3, $4)",
                        nutrient_rows,
                    )
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
    pool = await get_pool()
    async with pool.acquire() as conn:
        entry = await conn.fetchrow(
            "SELECT * FROM food_log WHERE id = $1 AND user_id = $2 AND source = 'meal' AND source_id = $3",
            food_log_id, user_id, str(meal_id),
        )
        if entry is None:
            raise HTTPException(status_code=404, detail="Combined meal entry not found for this meal_id/food_log_id")

        meal, items = await _get_meal_with_items(conn, meal_id, user_id)
        if meal is None:
            raise HTTPException(status_code=404, detail="Meal not found")

        food_log_ids = []
        async with conn.transaction():
            await conn.execute("DELETE FROM food_log WHERE id = $1", food_log_id)
            for item in items:
                new_id = await conn.fetchval(
                    """INSERT INTO food_log (user_id, date, meal, food_name, source, source_id,
                           serving_size, serving_unit, calories, nutrients_json)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                       RETURNING id""",
                    user_id, entry["date"], entry["meal"], item["food_name"], item["source"], item["source_id"],
                    item["serving_size"], item["serving_unit"], item["calories"], json.dumps(item["nutrients"]),
                )
                food_log_ids.append(new_id)
                nutrient_rows = [
                    (new_id, name, info["value"], info["unit"]) for name, info in item["nutrients"].items()
                ]
                if nutrient_rows:
                    await conn.executemany(
                        "INSERT INTO food_log_nutrients (food_log_id, nutrient_name, value, unit) VALUES ($1, $2, $3, $4)",
                        nutrient_rows,
                    )
    return {"status": "exploded", "food_log_ids": food_log_ids}
