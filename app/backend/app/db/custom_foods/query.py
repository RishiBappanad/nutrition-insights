import json

from .. import get_pool, insert_returning, update_with_ownership_returning, delete_with_ownership_returning
from ...nutrient_facts import write_nutrients, read_nutrients, delete_nutrient_facts
from ...domain_events import log_domain_event


async def create_custom_food(
    user_id: int, food_name, brand, reference_amount, reference_unit, reference_grams,
    category, calories, nutrients: dict,
) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            food_id = await insert_returning(conn, "custom_foods", {
                "user_id": user_id, "food_name": food_name, "brand": brand,
                "reference_amount": reference_amount, "reference_unit": reference_unit,
                "reference_grams": reference_grams, "category": category, "calories": calories,
                "nutrients_json": json.dumps(nutrients),
            })
            await write_nutrients(conn, "custom_food", food_id, nutrients)
            await log_domain_event(
                conn, user_id, "custom_food", food_id, "created",
                category=category, amount=calories, label=food_name, metadata={"brand": brand},
            )
    return food_id


async def list_custom_foods(user_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT * FROM custom_foods WHERE user_id = $1 ORDER BY food_name", user_id
        )


async def get_custom_food(food_id: int, user_id: int):
    """Returns (row, nutrients), or (None, None) if not found / not owned
    by this user."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM custom_foods WHERE id = $1 AND user_id = $2", food_id, user_id
        )
        if row is None:
            return None, None
        nutrients = await read_nutrients(conn, "custom_food", food_id)
    return row, nutrients


async def update_custom_food(
    food_id: int, user_id: int, food_name, brand, reference_amount, reference_unit,
    reference_grams, category, calories, nutrients: dict,
) -> bool:
    """Returns True if a matching row was found and updated, False if not
    (in which case no update runs)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            updated = await update_with_ownership_returning(
                conn, "custom_foods", food_id, user_id,
                {
                    "food_name": food_name, "brand": brand, "reference_amount": reference_amount,
                    "reference_unit": reference_unit, "reference_grams": reference_grams,
                    "category": category, "calories": calories, "nutrients_json": json.dumps(nutrients),
                },
                ["food_name", "category", "calories"],
            )
            if updated is None:
                return False

            await delete_nutrient_facts(conn, "custom_food", food_id)
            await write_nutrients(conn, "custom_food", food_id, nutrients)
            await log_domain_event(
                conn, user_id, "custom_food", food_id, "updated",
                category=updated["category"], amount=updated["calories"], label=updated["food_name"],
                metadata={"brand": brand},
            )
    return True


async def delete_custom_food(food_id: int, user_id: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            deleted = await delete_with_ownership_returning(
                conn, "custom_foods", food_id, user_id, ["food_name", "category", "calories"],
            )
            if deleted:
                await delete_nutrient_facts(conn, "custom_food", food_id)
                await log_domain_event(
                    conn, user_id, "custom_food", food_id, "deleted",
                    category=deleted["category"], amount=deleted["calories"], label=deleted["food_name"],
                )
