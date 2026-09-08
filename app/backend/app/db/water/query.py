"""water_log CRUD, plus the one user_profile read water needs to resolve a
daily target. Lifted verbatim from routers/water.py — the domain-query
extraction described in the TrackStack consolidation plan."""
from .. import get_pool


async def insert_water_log(user_id: int, date: str, amount_ml: float) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "INSERT INTO water_log (user_id, date, amount_ml) VALUES ($1, $2, $3) RETURNING id",
            user_id, date, amount_ml,
        )


async def list_water_log(user_id: int, date: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT id, amount_ml, logged_at FROM water_log WHERE user_id = $1 AND date = $2 ORDER BY logged_at",
            user_id, date,
        )


async def get_water_profile(user_id: int):
    """Returns the (sex, water_target_ml) row from user_profile, or None
    if the user hasn't set up a profile yet."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT sex, water_target_ml FROM user_profile WHERE user_id = $1", user_id
        )


async def delete_water_log(entry_id: int, user_id: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM water_log WHERE id = $1 AND user_id = $2", entry_id, user_id
        )
