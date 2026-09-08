"""Custom food items API: user-defined foods with manually entered
nutrients, stored like a food source alongside USDA/CNF (source='custom').

This is the shared "food reference" abstraction recipes, meals, and the
label scanner all build on — a custom food is referenced the same way a
USDA food is (source + source_id) everywhere else in the app (food_log,
pantry_items, recipe_items, meal_items)."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..routers.auth import get_current_user
from ..db.custom_foods import query as custom_foods_query
from ..food_category import FoodCategory, DEFAULT_CATEGORY

router = APIRouter()


class CustomFoodRequest(BaseModel):
    food_name: str
    brand: Optional[str] = None
    reference_amount: float = 1.0
    reference_unit: str = "serving"
    reference_grams: Optional[float] = None  # None if this food has no known gram weight
    # A custom food has no ingredients to compute a default category
    # from (unlike recipes/meals), so this is simpler than those: an
    # explicit value is used as-is, otherwise DEFAULT_CATEGORY. No
    # category_is_custom column here for the same reason -- there's no
    # auto-computed value it would ever need to distinguish itself from.
    category: Optional[FoodCategory] = None
    # `calories` is the sole top-level numeric field. Protein/carbs/fat/
    # fiber belong in nutrients under their standard USDA names.
    calories: float = 0
    nutrients: dict = {}


@router.post("")
async def create_custom_food(req: CustomFoodRequest, user_id: int = Depends(get_current_user)):
    food_id = await custom_foods_query.create_custom_food(
        user_id, req.food_name, req.brand, req.reference_amount, req.reference_unit,
        req.reference_grams, req.category or DEFAULT_CATEGORY, req.calories, req.nutrients,
    )
    return {"status": "created", "id": food_id}


@router.get("")
async def list_custom_foods(user_id: int = Depends(get_current_user)):
    rows = await custom_foods_query.list_custom_foods(user_id)
    return {"foods": [_row_to_food(r) for r in rows]}


@router.get("/{food_id}")
async def get_custom_food(food_id: int, user_id: int = Depends(get_current_user)):
    row, nutrients = await custom_foods_query.get_custom_food(food_id, user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Custom food not found")
    result = _row_to_food(row)
    result["nutrients"] = nutrients
    return result


@router.put("/{food_id}")
async def update_custom_food(food_id: int, req: CustomFoodRequest, user_id: int = Depends(get_current_user)):
    updated = await custom_foods_query.update_custom_food(
        food_id, user_id, req.food_name, req.brand, req.reference_amount, req.reference_unit,
        req.reference_grams, req.category or DEFAULT_CATEGORY, req.calories, req.nutrients,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Custom food not found")
    return {"status": "updated"}


@router.delete("/{food_id}")
async def delete_custom_food(food_id: int, user_id: int = Depends(get_current_user)):
    await custom_foods_query.delete_custom_food(food_id, user_id)
    return {"status": "deleted"}


def _row_to_food(r) -> dict:
    return {
        "id": r["id"],
        "food_name": r["food_name"],
        "brand": r["brand"],
        "reference_amount": r["reference_amount"],
        "reference_unit": r["reference_unit"],
        "reference_grams": r["reference_grams"],
        "category": r["category"],
        "calories": r["calories"],
    }
