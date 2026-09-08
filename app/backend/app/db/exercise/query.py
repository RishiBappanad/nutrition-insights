from .. import get_pool


async def list_exercise_log(user_id: int, date: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT * FROM exercise_log WHERE user_id = $1 AND date = $2 ORDER BY created_at",
            user_id, date,
        )


async def update_exercise_entry(
    entry_id: int, user_id: int, date, activity_name, duration_minutes, calories_burned, notes
):
    """Returns the pre-update row, or None if no matching entry exists (in
    which case no update is performed). Kept as fetch+update in one
    acquired connection, matching the original grouping."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT * FROM exercise_log WHERE id = $1 AND user_id = $2", entry_id, user_id
        )
        if existing is None:
            return None

        await conn.execute(
            """UPDATE exercise_log SET
                   date = COALESCE($1, date),
                   activity_name = COALESCE($2, activity_name),
                   duration_minutes = COALESCE($3, duration_minutes),
                   calories_burned = COALESCE($4, calories_burned),
                   notes = COALESCE($5, notes),
                   updated_at = now()
               WHERE id = $6 AND user_id = $7""",
            date, activity_name, duration_minutes, calories_burned, notes,
            entry_id, user_id,
        )
    return existing


async def delete_exercise_entry(entry_id: int, user_id: int) -> str:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.execute(
            "DELETE FROM exercise_log WHERE id = $1 AND user_id = $2", entry_id, user_id
        )
