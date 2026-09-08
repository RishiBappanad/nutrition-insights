from .. import get_pool


async def upsert_note(user_id: int, date: str, text: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO diary_notes (user_id, date, text, updated_at)
               VALUES ($1, $2, $3, now())
               ON CONFLICT (user_id, date) DO UPDATE SET text = EXCLUDED.text, updated_at = now()""",
            user_id, date, text,
        )


async def get_note(user_id: int, date: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT text, attachment_url, updated_at FROM diary_notes WHERE user_id = $1 AND date = $2",
            user_id, date,
        )


async def delete_note(user_id: int, date: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM diary_notes WHERE user_id = $1 AND date = $2", user_id, date
        )
