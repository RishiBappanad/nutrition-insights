"""Custom food items API: user-defined foods with manually entered
nutrients, stored like a food source alongside USDA/CNF (source='custom').

This is the shared "food reference" abstraction recipes, meals, and the
label scanner all build on — a custom food is referenced the same way a
USDA food is (source + source_id) everywhere else in the app (food_log,
pantry_items, recipe_items, meal_items)."""
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..routers.auth import get_current_user
from ..db import get_pool
from ..nutrient_groups import order_nutrients

router = APIRouter()


class CustomFoodRequest(BaseModel):
    food_name: str
    brand: Optional[str] = None
    reference_amount: float = 1.0
    reference_unit: str = "serving"
    reference_grams: Optional[float] = None  # None if this food has no known gram weight
    # `calories` is the sole top-level numeric field. Protein/carbs/fat/
    # fiber belong in nutrients under their standard USDA names.
    calories: float = 0
    nutrients: dict = {}


def _nutrients_to_rows(custom_food_id: int, nutrients: dict) -> list[tuple]:
    """Same normalization rules as routers/food.py's nutrients_to_rows —
    duplicated rather than imported cross-module here since the target
    table differs (custom_food_nutrients vs food_log_nutrients) and the
    row shape needs its own first column name; the validation logic
    itself is intentionally identical."""
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
        rows.append((custom_food_id, name, value, info.get("unit", "")))
    return rows


@router.post("")
async def create_custom_food(req: CustomFoodRequest, user_id: int = Depends(get_current_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            food_id = await conn.fetchval(
                """INSERT INTO custom_foods (user_id, food_name, brand, reference_amount, reference_unit,
                       reference_grams, calories, nutrients_json)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                   RETURNING id""",
                user_id, req.food_name, req.brand, req.reference_amount, req.reference_unit,
                req.reference_grams, req.calories, json.dumps(req.nutrients),
            )
            rows = _nutrients_to_rows(food_id, req.nutrients)
            if rows:
                await conn.executemany(
                    "INSERT INTO custom_food_nutrients (custom_food_id, nutrient_name, value, unit) VALUES ($1, $2, $3, $4)",
                    rows,
                )
    return {"status": "created", "id": food_id}


@router.get("")
async def list_custom_foods(user_id: int = Depends(get_current_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM custom_foods WHERE user_id = $1 ORDER BY food_name", user_id
        )
    return {"foods": [_row_to_food(r) for r in rows]}


@router.get("/{food_id}")
async def get_custom_food(food_id: int, user_id: int = Depends(get_current_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM custom_foods WHERE id = $1 AND user_id = $2", food_id, user_id
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Custom food not found")
        nutrient_rows = await conn.fetch(
            "SELECT nutrient_name, value, unit FROM custom_food_nutrients WHERE custom_food_id = $1", food_id
        )
    result = _row_to_food(row)
    result["nutrients"] = order_nutrients({n["nutrient_name"]: {"value": n["value"], "unit": n["unit"]} for n in nutrient_rows})
    return result


@router.put("/{food_id}")
async def update_custom_food(food_id: int, req: CustomFoodRequest, user_id: int = Depends(get_current_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            existing = await conn.fetchrow(
                "SELECT id FROM custom_foods WHERE id = $1 AND user_id = $2", food_id, user_id
            )
            if existing is None:
                raise HTTPException(status_code=404, detail="Custom food not found")

            await conn.execute(
                """UPDATE custom_foods SET food_name=$1, brand=$2, reference_amount=$3, reference_unit=$4,
                       reference_grams=$5, calories=$6,
                       nutrients_json=$7, updated_at=now()
                   WHERE id = $8""",
                req.food_name, req.brand, req.reference_amount, req.reference_unit, req.reference_grams,
                req.calories, json.dumps(req.nutrients), food_id,
            )
            await conn.execute("DELETE FROM custom_food_nutrients WHERE custom_food_id = $1", food_id)
            rows = _nutrients_to_rows(food_id, req.nutrients)
            if rows:
                await conn.executemany(
                    "INSERT INTO custom_food_nutrients (custom_food_id, nutrient_name, value, unit) VALUES ($1, $2, $3, $4)",
                    rows,
                )
    return {"status": "updated"}


@router.delete("/{food_id}")
async def delete_custom_food(food_id: int, user_id: int = Depends(get_current_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM custom_foods WHERE id = $1 AND user_id = $2", food_id, user_id)
    return {"status": "deleted"}


def _row_to_food(r) -> dict:
    return {
        "id": r["id"],
        "food_name": r["food_name"],
        "brand": r["brand"],
        "reference_amount": r["reference_amount"],
        "reference_unit": r["reference_unit"],
        "reference_grams": r["reference_grams"],
        "calories": r["calories"],
    }
