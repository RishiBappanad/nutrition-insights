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
    calories: float = 0
    protein: float = 0
    carbs: float = 0
    fat: float = 0
    fiber: float = 0
    nutrients: dict = {}


class MealRequest(BaseModel):
    name: str
    items: list[MealItemRequest] = []


class LogMealRequest(BaseModel):
    date: str
    meal: str = "Lunch"


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
                   calories, protein, carbs, fat, fiber, nutrients_json)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
               RETURNING id""",
            meal_id, item.food_name, item.source, item.source_id, item.serving_size, item.serving_unit,
            item.calories, item.protein, item.carbs, item.fat, item.fiber, json.dumps(item.nutrients),
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
            "protein": r["protein"],
            "carbs": r["carbs"],
            "fat": r["fat"],
            "fiber": r["fiber"],
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
    """Logs every item in the meal as its own food_log entry, all with the
    same date/meal — a meal is a flat group of real foods logged
    together, not one aggregated entry like a recipe serving is. This
    means each item still shows individually in the diary (and can be
    edited/deleted individually afterward), matching what "log my usual
    breakfast" actually means to a user."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        meal, items = await _get_meal_with_items(conn, meal_id, user_id)
        if meal is None:
            raise HTTPException(status_code=404, detail="Meal not found")

        food_log_ids = []
        async with conn.transaction():
            for item in items:
                food_log_id = await conn.fetchval(
                    """INSERT INTO food_log (user_id, date, meal, food_name, source, source_id,
                           serving_size, serving_unit, calories, protein, carbs, fat, fiber, nutrients_json)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
                       RETURNING id""",
                    user_id, req.date, req.meal, item["food_name"], item["source"], item["source_id"],
                    item["serving_size"], item["serving_unit"], item["calories"], item["protein"],
                    item["carbs"], item["fat"], item["fiber"], json.dumps(item["nutrients"]),
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
    return {"status": "logged", "food_log_ids": food_log_ids}
