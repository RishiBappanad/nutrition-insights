import json

from .. import get_pool
from ...nutrient_facts import write_nutrients, read_nutrients, delete_nutrient_facts


async def create_custom_food(
    user_id: int, food_name, brand, reference_amount, reference_unit, reference_grams,
    category, calories, nutrients: dict,
) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            food_id = await conn.fetchval(
                """INSERT INTO custom_foods (user_id, food_name, brand, reference_amount, reference_unit,
                       reference_grams, category, calories, nutrients_json)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                   RETURNING id""",
                user_id, food_name, brand, reference_amount, reference_unit,
                reference_grams, category, calories, json.dumps(nutrients),
            )
            await write_nutrients(conn, "custom_food", food_id, nutrients)
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
            existing = await conn.fetchrow(
                "SELECT id FROM custom_foods WHERE id = $1 AND user_id = $2", food_id, user_id
            )
            if existing is None:
                return False

            await conn.execute(
                """UPDATE custom_foods SET food_name=$1, brand=$2, reference_amount=$3, reference_unit=$4,
                       reference_grams=$5, category=$6, calories=$7,
                       nutrients_json=$8, updated_at=now()
                   WHERE id = $9""",
                food_name, brand, reference_amount, reference_unit, reference_grams,
                category, calories, json.dumps(nutrients), food_id,
            )
            await delete_nutrient_facts(conn, "custom_food", food_id)
            await write_nutrients(conn, "custom_food", food_id, nutrients)
    return True


async def delete_custom_food(food_id: int, user_id: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await delete_nutrient_facts(conn, "custom_food", food_id)
            await conn.execute("DELETE FROM custom_foods WHERE id = $1 AND user_id = $2", food_id, user_id)
