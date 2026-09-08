from .. import get_pool


async def list_lift_orm(user_id: int, exercise: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT date, orm FROM lift_orm WHERE user_id = $1 AND exercise = $2 ORDER BY date",
            user_id, exercise,
        )


async def reset_user_nutrition_lift_data(user_id: int) -> None:
    """Deletes nutrient_facts (food_log-owned rows), daily_nutrition,
    lift_orm, and food_log for this user in one atomic transaction --
    kept as a single function since splitting it would break that
    atomicity guarantee."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM nutrient_facts WHERE owner_type = 'food_log' "
                "AND owner_id IN (SELECT id FROM food_log WHERE user_id = $1)",
                user_id,
            )
            await conn.execute("DELETE FROM daily_nutrition WHERE user_id = $1", user_id)
            await conn.execute("DELETE FROM lift_orm WHERE user_id = $1", user_id)
            await conn.execute("DELETE FROM food_log WHERE user_id = $1", user_id)
