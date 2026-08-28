import csv
from pathlib import Path
from fastapi import APIRouter, Depends
from ..routers.auth import get_current_user
from ..user_db import query_nutrition, query_orm, get_nutrition_metrics, get_exercises, upsert_daily_nutrition, upsert_lift_orm, get_metric_series
from ..db import get_pool

router = APIRouter()

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def user_data_dir(user_id: int) -> Path:
    d = BACKEND_ROOT / "app_data" / f"user_{user_id}"
    d.mkdir(parents=True, exist_ok=True)
    return d


@router.get("/bmr")
async def get_bmr(user_id: int = Depends(get_current_user)):
    """Get current calculated BMR, sourced from Postgres (tdee_log table)
    — previously read a local CSV file with no persistent storage behind
    it, so BMR was computed against whatever partial/reset history
    happened to survive on the container instance handling the request."""
    from ..user_db import get_tdee_log as _get_tdee_log_rows

    records = await _get_tdee_log_rows(user_id)

    import sys
    sys.path.insert(0, str(BACKEND_ROOT))
    from tdee import calculate_bmr

    bmr = calculate_bmr(records=records)
    return {"bmr": bmr if isinstance(bmr, (int, float)) else None, "message": str(bmr)}


@router.get("/tdee-log")
async def get_tdee_log(user_id: int = Depends(get_current_user)):
    """Get TDEE tracking log as JSON, from Postgres."""
    from ..user_db import get_tdee_log as _get_tdee_log_rows

    records = await _get_tdee_log_rows(user_id)
    return {"entries": records}


@router.get("/workouts")
async def get_workouts(user_id: int = Depends(get_current_user), limit: int = 20):
    """Get recent Hevy workouts."""
    csv_path = user_data_dir(user_id) / "hevy_workouts.csv"
    if not csv_path.exists():
        return {"workouts": []}

    with open(csv_path) as f:
        rows = list(csv.DictReader(f))
    return {"workouts": rows[-limit:]}


def _compute_orm(weight: float, reps: int) -> float:
    """Compute estimated 1RM using Brzycki (reps<=10) or Epley (reps>10)."""
    if reps <= 0 or weight <= 0:
        return 0
    if reps == 1:
        return weight
    if reps <= 10:
        return weight / (1.0278 - 0.0278 * reps)
    return weight * (1 + reps / 30)


def _parse_hevy_date(date_str: str) -> str:
    """Parse 'Jun 5, 2026, 8:33 AM' to '2026-06-05'."""
    from datetime import datetime
    try:
        dt = datetime.strptime(date_str.strip(), "%b %d, %Y, %I:%M %p")
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return ""


def _get_orm_data(csv_path: Path) -> dict:
    """Process hevy CSV into {exercise: [{date, orm}]} with max ORM per day."""
    from collections import defaultdict

    if not csv_path.exists():
        return {}

    daily_max = defaultdict(lambda: defaultdict(float))  # exercise -> date -> max_orm

    with open(csv_path) as f:
        for row in csv.DictReader(f):
            exercise = row.get("exercise_title", "").strip()
            weight_str = row.get("weight_lbs", "")
            reps_str = row.get("reps", "")
            start_time = row.get("start_time", "")

            if not exercise or not weight_str or not reps_str:
                continue

            try:
                weight = float(weight_str)
                reps = int(reps_str)
            except (ValueError, TypeError):
                continue

            date = _parse_hevy_date(start_time)
            if not date:
                continue

            orm = _compute_orm(weight, reps)
            if orm > daily_max[exercise][date]:
                daily_max[exercise][date] = round(orm, 1)

    # Convert to sorted lists
    result = {}
    for exercise, dates in daily_max.items():
        result[exercise] = sorted(
            [{"date": d, "orm": v} for d, v in dates.items()],
            key=lambda x: x["date"]
        )
    return result


@router.get("/orm")
async def get_orm(user_id: int = Depends(get_current_user)):
    """Get ORM data per exercise."""
    csv_path = user_data_dir(user_id) / "hevy_workouts.csv"
    return _get_orm_data(csv_path)


@router.get("/chart")
async def get_chart_data(user_id: int = Depends(get_current_user), metrics: str = "", lookback: int = 1):
    """Get chart data from Postgres. Rolling average applied via SQL for nutrition/burn metrics.
    ORM metrics are returned raw (no rolling avg)."""
    # NOTE: Auto-migration removed - data is only populated via explicit sync
    # This prevents new users from seeing stale data

    requested = [m.strip() for m in metrics.split(",") if m.strip()]
    lookback = max(1, min(3, lookback))

    all_nutrition = await get_nutrition_metrics(user_id)
    all_exercises = await get_exercises(user_id)

    # Split requested into nutrition vs exercise vs biometrics
    nutrition_requested = [m for m in requested if m in all_nutrition]
    exercise_requested = [m for m in requested if m in all_exercises]
    biometrics_requested = [m for m in requested if m == "Weight (lbs)"]

    series = {}

    # Biometrics: raw, no rolling avg
    if biometrics_requested:
        series.update(await query_nutrition(user_id, biometrics_requested, 1))

    # Nutrition/burn: apply rolling avg via SQL
    if nutrition_requested:
        series.update(await query_nutrition(user_id, nutrition_requested, lookback))

    # Exercise ORM: raw, no rolling avg
    for ex in exercise_requested:
        orm_data = await query_orm(user_id, ex)
        series.update(orm_data)

    categories = {
        "biometrics": ["Weight (lbs)"],
        "nutrition": [m for m in all_nutrition if m != "Weight (lbs)"],
        "exercise": all_exercises,
    }

    return {"series": series, "categories": categories}


@router.get("/lift-insights")
async def get_lift_insights(
    user_id: int = Depends(get_current_user),
    exercise: str = "",
    nutrition_metric: str = "Energy (kcal)",
    lookback: int = 2,
):
    """Pair each lift day's ORM with a rolling average of a nutrition metric over lookback days prior."""
    exercises = await get_exercises(user_id)
    nutrition_metrics = await get_nutrition_metrics(user_id)

    if not exercise:
        return {"exercises": exercises, "nutrition_metrics": nutrition_metrics, "data": []}

    lookback = max(1, min(3, lookback))

    # Merged daily_nutrition + food_log series (see user_db.get_metric_series)
    # -- same fix as GET /chart: daily_nutrition alone is only ever
    # populated by an explicit Cronometer sync, so a manual-only logger
    # would see every lift day correlate against nothing.
    metric_series = await get_metric_series(user_id, nutrition_metric)

    pool = await get_pool()
    async with pool.acquire() as conn:
        # Get ORM dates for this exercise
        orm_rows = await conn.fetch(
            "SELECT date, orm FROM lift_orm WHERE user_id = $1 AND exercise = $2 ORDER BY date",
            user_id, exercise,
        )

    # For each lift day, get rolling avg of nutrition metric from prior days
    from datetime import datetime, timedelta
    results = []
    for row in orm_rows:
        lift_date = row["date"]
        try:
            dt = datetime.strptime(lift_date, "%Y-%m-%d")
        except ValueError:
            continue

        prior_dates = [(dt - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(1, lookback + 1)]
        vals = [metric_series[d] for d in prior_dates if d in metric_series]

        if vals:
            avg = round(sum(vals) / len(vals), 1)
            results.append({"date": lift_date, "orm": row["orm"], "avg_metric": avg})

    return {
        "exercises": exercises,
        "nutrition_metrics": nutrition_metrics,
        "lookback": lookback,
        "metric": nutrition_metric,
        "exercise": exercise,
        "data": results,
    }


@router.delete("/reset")
async def reset_user_data(user_id: int = Depends(get_current_user)):
    """Delete all nutrition and lift data for the current user. Does not delete credentials."""
    data_dir = user_data_dir(user_id)

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM nutrient_facts WHERE owner_type = 'food_log' "
                "AND owner_id IN (SELECT id FROM food_log WHERE user_id = $1)",
                user_id,
            )
            await conn.execute("DELETE FROM daily_nutrition WHERE user_id = $1", user_id)
            await conn.execute("DELETE FROM lift_orm WHERE user_id = $1", user_id)
            await conn.execute("DELETE FROM food_log WHERE user_id = $1", user_id)

    # Remove CSV files (from old syncs) but keep the directory
    for pattern in ["cronometer_*.csv", "hevy_workouts.csv", "tdee_tracking_log.csv"]:
        for f in data_dir.glob(pattern):
            f.unlink()

    return {"status": "reset", "user_id": user_id}
