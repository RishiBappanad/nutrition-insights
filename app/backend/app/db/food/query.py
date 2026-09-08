from .. import get_pool
from ...nutrient_facts import read_nutrients_bulk, delete_nutrient_facts


async def search_recipes_by_name(user_id: int, query: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT id, name, servings_per_batch FROM recipes WHERE user_id = $1 AND name ILIKE $2 ORDER BY name",
            user_id, f"%{query}%",
        )


async def search_meals_by_name(user_id: int, query: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT id, name FROM meals WHERE user_id = $1 AND name ILIKE $2 ORDER BY name",
            user_id, f"%{query}%",
        )


async def list_food_log(user_id: int, date: str):
    """Returns (rows, nutrients_by_entry) -- kept together since the
    original call read both within the same acquired connection."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM food_log WHERE user_id = $1 AND date = $2 ORDER BY id",
            user_id, date,
        )
        entry_ids = [r["id"] for r in rows]
        nutrients_by_entry = await read_nutrients_bulk(conn, "food_log", entry_ids)
    return rows, nutrients_by_entry


async def delete_food_entry(entry_id: int, user_id: int) -> None:
    """nutrient_facts has no FK to cascade automatically (see
    app/nutrient_facts.py), so its rows for this entry are deleted
    explicitly first, in the same transaction as the food_log delete."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await delete_nutrient_facts(conn, "food_log", entry_id)
            await conn.execute(
                "DELETE FROM food_log WHERE id = $1 AND user_id = $2", entry_id, user_id
            )
