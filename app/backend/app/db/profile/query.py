from .. import get_pool


async def upsert_profile(
    user_id: int, age: int, sex: str, height_cm, weight_kg, activity_level, water_target_ml
) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO user_profile (user_id, age, sex, height_cm, weight_kg, activity_level, water_target_ml, updated_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, now())
               ON CONFLICT (user_id) DO UPDATE SET
                   age = EXCLUDED.age,
                   sex = EXCLUDED.sex,
                   height_cm = EXCLUDED.height_cm,
                   weight_kg = EXCLUDED.weight_kg,
                   activity_level = EXCLUDED.activity_level,
                   water_target_ml = EXCLUDED.water_target_ml,
                   updated_at = now()""",
            user_id, age, sex, height_cm, weight_kg, activity_level, water_target_ml,
        )


async def get_profile(user_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM user_profile WHERE user_id = $1", user_id)
