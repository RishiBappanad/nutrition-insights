from .. import get_pool, delete_with_ownership_returning, update_with_ownership_returning
from ..sql_builder import select_clause, where_clause, order_by_clause
from ...domain_events import log_domain_event


async def list_exercise_log(user_id: int, date: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        sql = (
            select_clause("exercise_log")
            + where_clause(["user_id = $1", "date = $2"])
            + order_by_clause("created_at")
        )
        return await conn.fetch(sql, user_id, date)


async def update_exercise_entry(
    entry_id: int, user_id: int, date, activity_name, duration_minutes, calories_burned, notes
):
    """Returns the post-update row, or None if no matching entry exists
    (in which case no update is performed) -- callers only ever check
    this for truthiness (found vs. not found), so switching from the
    original pre-update row to update_with_ownership_returning's
    post-update RETURNING doesn't change any caller's behavior."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await update_with_ownership_returning(
                conn, "exercise_log", entry_id, user_id,
                {"date": date, "activity_name": activity_name, "duration_minutes": duration_minutes,
                 "calories_burned": calories_burned, "notes": notes},
                ["date", "activity_name", "calories_burned", "source", "source_id", "duration_minutes", "notes"],
            )
            if row:
                await log_domain_event(
                    conn, user_id, "exercise_log", entry_id, "updated",
                    amount=row["calories_burned"], label=row["activity_name"],
                    source=row["source"], source_id=row["source_id"],
                    metadata={"duration_minutes": row["duration_minutes"], "notes": row["notes"]},
                    occurred_at=row["date"],
                )
    return row


async def delete_exercise_entry(entry_id: int, user_id: int):
    """Returns the deleted row, or None if no matching entry existed."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            deleted = await delete_with_ownership_returning(
                conn, "exercise_log", entry_id, user_id,
                ["date", "activity_name", "calories_burned", "source", "source_id", "duration_minutes", "notes"],
            )
            if deleted:
                await log_domain_event(
                    conn, user_id, "exercise_log", entry_id, "deleted",
                    amount=deleted["calories_burned"], label=deleted["activity_name"],
                    source=deleted["source"], source_id=deleted["source_id"],
                    metadata={"duration_minutes": deleted["duration_minutes"], "notes": deleted["notes"]},
                    occurred_at=deleted["date"],
                )
    return deleted
