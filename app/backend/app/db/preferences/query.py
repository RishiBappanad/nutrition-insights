from .. import get_pool


async def get_preferences_row(user_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM user_preferences WHERE user_id = $1", user_id)


async def upsert_preferences(
    user_id: int, colors_json: str, threshold, unit_system, macro_chart_style, important_nutrients_json
) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO user_preferences (user_id, colors_json, sufficiency_threshold_pct, unit_system, macro_chart_style, important_nutrients_json, updated_at)
               VALUES ($1, $2, $3, $4, $5, $6, now())
               ON CONFLICT (user_id) DO UPDATE SET
                   colors_json = EXCLUDED.colors_json,
                   sufficiency_threshold_pct = EXCLUDED.sufficiency_threshold_pct,
                   unit_system = EXCLUDED.unit_system,
                   macro_chart_style = EXCLUDED.macro_chart_style,
                   important_nutrients_json = EXCLUDED.important_nutrients_json,
                   updated_at = now()""",
            user_id, colors_json, threshold, unit_system, macro_chart_style, important_nutrients_json,
        )


async def delete_preferences(user_id: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM user_preferences WHERE user_id = $1", user_id)
