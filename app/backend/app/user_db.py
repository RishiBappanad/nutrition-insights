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
