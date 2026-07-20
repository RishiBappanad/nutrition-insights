"""User-scoped nutrition and lift data, stored in shared Postgres tables.

Isolation is enforced via a user_id column + WHERE clause on every query
(row-level scoping), replacing the old per-user SQLite-file-per-user model.
"""
import asyncpg

from .db import get_pool


async def upsert_daily_nutrition(user_id: int, date: str, metrics: dict):
    """Insert/replace nutrition metrics for a date. metrics = {metric_name: value}"""
    pool = await get_pool()
    rows = [(user_id, date, k, v) for k, v in metrics.items() if v is not None]
    if not rows:
        return
    async with pool.acquire() as conn:
        await conn.executemany(
            """INSERT INTO daily_nutrition (user_id, date, metric, value)
               VALUES ($1, $2, $3, $4)
               ON CONFLICT (user_id, date, metric) DO UPDATE SET value = EXCLUDED.value""",
            rows,
        )


async def upsert_lift_orm(user_id: int, date: str, exercise: str, orm: float):
    """Insert/replace ORM for an exercise on a date (keeps max)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT orm FROM lift_orm WHERE user_id = $1 AND date = $2 AND exercise = $3",
            user_id, date, exercise,
        )
        if existing is None or orm > existing["orm"]:
            await conn.execute(
                """INSERT INTO lift_orm (user_id, date, exercise, orm)
                   VALUES ($1, $2, $3, $4)
                   ON CONFLICT (user_id, date, exercise) DO UPDATE SET orm = EXCLUDED.orm""",
                user_id, date, exercise, orm,
            )


async def query_nutrition(user_id: int, metrics: list, lookback: int = 1) -> dict:
    """Query nutrition metrics with optional rolling average.
    Returns {metric: [{date, value}]}"""
    pool = await get_pool()
    result = {}
    async with pool.acquire() as conn:
        for metric in metrics:
            if lookback <= 1:
                rows = await conn.fetch(
                    "SELECT date, value FROM daily_nutrition WHERE user_id = $1 AND metric = $2 ORDER BY date",
                    user_id, metric,
                )
                result[metric] = [{"date": r["date"], "value": round(r["value"], 1)} for r in rows]
            else:
                rows = await conn.fetch(
                    """SELECT date,
                        AVG(value) OVER (
                            ORDER BY date
                            ROWS BETWEEN $1 PRECEDING AND CURRENT ROW
                        ) as avg_value
                    FROM daily_nutrition
                    WHERE user_id = $2 AND metric = $3
                    ORDER BY date""",
                    lookback - 1, user_id, metric,
                )
                result[metric] = [{"date": r["date"], "value": round(r["avg_value"], 1)} for r in rows]
    return result


async def query_orm(user_id: int, exercise: str = None) -> dict:
    """Query ORM data. If exercise specified, returns [{date, orm}].
    Otherwise returns {exercise: [{date, orm}]}."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        if exercise:
            rows = await conn.fetch(
                "SELECT date, orm FROM lift_orm WHERE user_id = $1 AND exercise = $2 ORDER BY date",
                user_id, exercise,
            )
            return {exercise: [{"date": r["date"], "value": r["orm"]} for r in rows]}
        else:
            ex_rows = await conn.fetch(
                "SELECT DISTINCT exercise FROM lift_orm WHERE user_id = $1 ORDER BY exercise",
                user_id,
            )
            result = {}
            for r in ex_rows:
                ex = r["exercise"]
                data = await conn.fetch(
                    "SELECT date, orm FROM lift_orm WHERE user_id = $1 AND exercise = $2 ORDER BY date",
                    user_id, ex,
                )
                result[ex] = [{"date": d["date"], "value": d["orm"]} for d in data]
            return result


async def get_exercises(user_id: int) -> list:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT DISTINCT exercise FROM lift_orm WHERE user_id = $1 ORDER BY exercise",
            user_id,
        )
        return [r["exercise"] for r in rows]


async def get_nutrition_metrics(user_id: int) -> list:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT DISTINCT metric FROM daily_nutrition WHERE user_id = $1 ORDER BY metric",
            user_id,
        )
        return [r["metric"] for r in rows]


async def upsert_tdee_log(user_id: int, date: str, weight_lbs: float = None,
                           calories_consumed: float = None, active_calories_burned: float = None):
    """Insert/merge a day's TDEE tracking fields. Partial updates merge
    with any existing row for that date (e.g. a sync that only has
    biometrics data for today shouldn't null out calories_consumed that
    a different sync already wrote for the same day) -- COALESCE keeps
    the existing value when the new value is NULL, matching the CSV
    version's merge-by-date behavior in routers/sync.py's
    _update_tdee_log."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO tdee_log (user_id, date, weight_lbs, calories_consumed, active_calories_burned)
               VALUES ($1, $2, $3, $4, $5)
               ON CONFLICT (user_id, date) DO UPDATE SET
                   weight_lbs = COALESCE(EXCLUDED.weight_lbs, tdee_log.weight_lbs),
                   calories_consumed = COALESCE(EXCLUDED.calories_consumed, tdee_log.calories_consumed),
                   active_calories_burned = COALESCE(EXCLUDED.active_calories_burned, tdee_log.active_calories_burned)""",
            user_id, date, weight_lbs, calories_consumed, active_calories_burned,
        )


async def get_tdee_log(user_id: int) -> list:
    """All TDEE tracking rows for a user, ordered by date -- the same
    shape calculate_bmr expects (Date/Weight_lbs/Calories_Consumed/
    Active_Calories_Burned), sourced from Postgres instead of a local
    CSV file."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT date, weight_lbs, calories_consumed, active_calories_burned "
            "FROM tdee_log WHERE user_id = $1 ORDER BY date",
            user_id,
        )
    return [
        {
            "Date": r["date"],
            "Weight_lbs": r["weight_lbs"],
            "Calories_Consumed": r["calories_consumed"],
            "Active_Calories_Burned": r["active_calories_burned"],
        }
        for r in rows
    ]
