from .. import get_pool
from ...nutrient_facts import read_nutrients_bulk


async def list_food_log_events(user_id: int, start: str, end: str):
    """Returns (food_rows, nutrients_by_entry) -- kept together since the
    original call read both within the same acquired connection."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        food_rows = await conn.fetch(
            "SELECT * FROM food_log WHERE user_id = $1 AND date >= $2 AND date <= $3 ORDER BY date",
            user_id, start, end,
        )
        entry_ids = [r["id"] for r in food_rows]
        nutrients_by_entry = await read_nutrients_bulk(conn, "food_log", entry_ids)
    return food_rows, nutrients_by_entry


async def list_exercise_log_events(user_id: int, start: str, end: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT * FROM exercise_log WHERE user_id = $1 AND date >= $2 AND date <= $3 ORDER BY date",
            user_id, start, end,
        )
