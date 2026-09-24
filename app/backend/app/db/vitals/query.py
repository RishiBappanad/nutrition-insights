"""Named query module for vital readings (rows of daily_nutrition whose
metric is a vital -- see app/vitals.py)."""
from .. import get_pool


async def list_vital_readings(user_id: int, metric_name: str, limit: int):
    """Newest-first (date, value) rows for one metric."""
    pool = await get_pool()
    return await pool.fetch(
        "SELECT date, value FROM daily_nutrition WHERE user_id = $1 AND metric = $2 ORDER BY date DESC LIMIT $3",
        user_id, metric_name, limit,
    )
