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


async def _daily_nutrition_series(conn, user_id: int, metric: str) -> dict:
    rows = await conn.fetch(
        "SELECT date, value FROM daily_nutrition WHERE user_id = $1 AND metric = $2",
        user_id, metric,
    )
    return {r["date"]: r["value"] for r in rows}


async def _food_log_series(conn, user_id: int, metric: str) -> dict:
    """Aggregate manually-logged food into a {date: value} series for one
    chartable nutrition metric. food_log/nutrient_facts is the single
    source of truth for "what was eaten" regardless of how it got logged
    (manual entry, a recipe, or a Cronometer diary import) -- unlike
    daily_nutrition, which only ever gets written by an explicit Cronometer
    sync. Calories is food_log's own top-level column (the sole numeric
    "amount" field per the Event Contract standardization); every other
    nutrient lives in nutrient_facts (owner_type='food_log') keyed by its
    USDA name."""
    if metric == "Energy (kcal)":
        rows = await conn.fetch(
            "SELECT date, SUM(calories) AS total FROM food_log "
            "WHERE user_id = $1 AND calories IS NOT NULL GROUP BY date",
            user_id,
        )
    else:
        rows = await conn.fetch(
            """SELECT fl.date, SUM(nf.value) AS total
               FROM nutrient_facts nf
               JOIN food_log fl ON fl.id = nf.owner_id AND nf.owner_type = 'food_log'
               WHERE fl.user_id = $1 AND nf.nutrient_name = $2
               GROUP BY fl.date""",
            user_id, metric,
        )
    return {r["date"]: r["total"] for r in rows}


async def get_metric_series(user_id: int, metric: str) -> dict:
    """Public, single-metric version of the daily_nutrition/food_log merge
    in query_nutrition below, for callers (e.g. GET /data/lift-insights)
    that need a plain {date: value} series rather than the chart
    endpoint's {metric: [{date, value}]} shape or its rolling average."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        synced = await _daily_nutrition_series(conn, user_id, metric)
        logged = await _food_log_series(conn, user_id, metric)
    return {**logged, **synced}


async def query_nutrition(user_id: int, metrics: list, lookback: int = 1) -> dict:
    """Query nutrition metrics with optional rolling average.

    Merges two sources per (date, metric): daily_nutrition (populated only
    by an explicit Cronometer sync) and a live aggregate over
    food_log/food_log_nutrients (populated by every logging path -- manual
    entry, recipes, and Cronometer diary import alike). daily_nutrition
    wins where both have a value for the same date, so already-synced days
    keep showing exactly what they show today; food_log fills in every
    other day. Without this fallback, a user who only ever logs food
    manually has an entirely empty daily_nutrition table and no chartable
    metrics at all, no matter how much they've logged -- this was the root
    cause of charts requiring a Cronometer sync to show anything.

    Rolling average is computed here in Python (not SQL) since it now
    windows over a merged, non-SQL-native series -- same semantics as the
    original window function (average over however many rows are actually
    present, not calendar days), just applied after the merge.

    Returns {metric: [{date, value}]}"""
    pool = await get_pool()
    result = {}
    async with pool.acquire() as conn:
        for metric in metrics:
            synced = await _daily_nutrition_series(conn, user_id, metric)
            logged = await _food_log_series(conn, user_id, metric)
            merged = {**logged, **synced}
            dates = sorted(merged)

            if lookback <= 1:
                result[metric] = [{"date": d, "value": round(merged[d], 1)} for d in dates]
            else:
                values = [merged[d] for d in dates]
                series = []
                for i, d in enumerate(dates):
                    window = values[max(0, i - (lookback - 1)):i + 1]
                    series.append({"date": d, "value": round(sum(window) / len(window), 1)})
                result[metric] = series
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
    """Every chartable nutrition metric name for this user: whatever
    Cronometer sync has written to daily_nutrition, PLUS "Energy (kcal)"
    and every nutrient name the user has ever logged via food_log directly
    -- see query_nutrition's docstring for why both sources matter. Without
    the food_log half, a user who has never synced Cronometer gets an
    empty metric list and the chart page has nothing to select at all."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        synced_rows = await conn.fetch(
            "SELECT DISTINCT metric FROM daily_nutrition WHERE user_id = $1",
            user_id,
        )
        logged_rows = await conn.fetch(
            """SELECT DISTINCT nf.nutrient_name AS metric
               FROM nutrient_facts nf
               JOIN food_log fl ON fl.id = nf.owner_id AND nf.owner_type = 'food_log'
               WHERE fl.user_id = $1""",
            user_id,
        )
        has_calories = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM food_log WHERE user_id = $1 AND calories IS NOT NULL)",
            user_id,
        )

    metrics = {r["metric"] for r in synced_rows} | {r["metric"] for r in logged_rows}
    if has_calories:
        metrics.add("Energy (kcal)")
    return sorted(metrics)


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
