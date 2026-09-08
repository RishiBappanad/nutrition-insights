from .. import get_pool


async def get_credentials(user_id: int):
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchrow("SELECT * FROM credentials WHERE user_id = $1", user_id)


async def list_cronometer_recipe_source_ids(user_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT source_id FROM recipes WHERE user_id = $1 AND source = 'Cronometer'", user_id
        )
