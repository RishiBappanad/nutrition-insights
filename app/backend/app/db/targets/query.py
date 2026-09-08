from .. import get_pool


async def upsert_macro_targets(
    user_id: int, mode, calorie_target, protein_g, carbs_g, fat_g, protein_pct, carbs_pct, fat_pct
) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO macro_target_settings
                   (user_id, mode, calorie_target, protein_g, carbs_g, fat_g, protein_pct, carbs_pct, fat_pct, updated_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, now())
               ON CONFLICT (user_id) DO UPDATE SET
                   mode = EXCLUDED.mode,
                   calorie_target = EXCLUDED.calorie_target,
                   protein_g = EXCLUDED.protein_g,
                   carbs_g = EXCLUDED.carbs_g,
                   fat_g = EXCLUDED.fat_g,
                   protein_pct = EXCLUDED.protein_pct,
                   carbs_pct = EXCLUDED.carbs_pct,
                   fat_pct = EXCLUDED.fat_pct,
                   updated_at = now()""",
            user_id, mode, calorie_target, protein_g, carbs_g, fat_g,
            protein_pct, carbs_pct, fat_pct,
        )


async def list_nutrient_targets(user_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT nutrient_name, unit, daily_target, max_threshold, is_custom FROM nutrition_targets "
            "WHERE user_id = $1 ORDER BY nutrient_name",
            user_id,
        )


async def update_nutrient_target(user_id: int, nutrient_name: str, daily_target, max_threshold, is_custom):
    """Returns the pre-update row (with `unit`), or None if the nutrient
    doesn't exist yet for this user (in which case no update runs). Kept
    as fetch+update in one acquired connection, matching the original."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT unit FROM nutrition_targets WHERE user_id = $1 AND nutrient_name = $2",
            user_id, nutrient_name,
        )
        if existing is None:
            return None
        await conn.execute(
            """UPDATE nutrition_targets SET daily_target = $1, max_threshold = $2, is_custom = $3, updated_at = now()
               WHERE user_id = $4 AND nutrient_name = $5""",
            daily_target, max_threshold, is_custom, user_id, nutrient_name,
        )
    return existing
