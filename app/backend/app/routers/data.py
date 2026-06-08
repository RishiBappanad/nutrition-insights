import csv
from pathlib import Path
from fastapi import APIRouter, Depends
from ..routers.auth import get_current_user

router = APIRouter()

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def user_data_dir(user_id: int) -> Path:
    d = BACKEND_ROOT / "app_data" / f"user_{user_id}"
    d.mkdir(parents=True, exist_ok=True)
    return d


@router.get("/bmr")
async def get_bmr(user_id: int = Depends(get_current_user)):
    """Get current calculated BMR."""
    csv_path = user_data_dir(user_id) / "tdee_tracking_log.csv"
    if not csv_path.exists():
        return {"bmr": None, "message": "No data yet. Run a sync first."}

    import sys
    sys.path.insert(0, str(BACKEND_ROOT))
    from tdee import calculate_bmr

    bmr = calculate_bmr(str(csv_path))
    return {"bmr": bmr if isinstance(bmr, (int, float)) else None, "message": str(bmr)}


@router.get("/tdee-log")
async def get_tdee_log(user_id: int = Depends(get_current_user)):
    """Get TDEE tracking log as JSON."""
    csv_path = user_data_dir(user_id) / "tdee_tracking_log.csv"
    if not csv_path.exists():
        return {"entries": []}

    with open(csv_path) as f:
        entries = list(csv.DictReader(f))
    return {"entries": entries}


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
    """Get chart data from SQLite. Rolling average applied via SQL for nutrition/burn metrics.
    ORM metrics are returned raw (no rolling avg)."""
    from ..user_db import get_user_db, query_nutrition, query_orm, get_nutrition_metrics, get_exercises, upsert_daily_nutrition, upsert_lift_orm

    data_dir = user_data_dir(user_id)
    conn = get_user_db(data_dir)

    # Auto-migrate: if DB is empty but CSVs exist, populate from them
    if not get_nutrition_metrics(conn):
        summary_path = data_dir / "cronometer_daily_summary.csv"
        if summary_path.exists():
            with open(summary_path) as f:
                for row in csv.DictReader(f):
                    if "Group" in row and row.get("Group", "").strip('"') != "Total":
                        continue
                    date = row.get("Date", "")
                    if not date:
                        continue
                    m = {}
                    for k, v in row.items():
                        if k in ("Date", "Group", "Completed") or not v:
                            continue
                        try:
                            m[k] = float(v)
                        except ValueError:
                            pass
                    if m:
                        upsert_daily_nutrition(conn, date, m)
        # Burn from exercises
        exercises_path = data_dir / "cronometer_exercises.csv"
        if exercises_path.exists():
            from collections import defaultdict
            daily_burn = defaultdict(float)
            with open(exercises_path) as f:
                for row in csv.DictReader(f):
                    if row.get("Calories Burned"):
                        daily_burn[row["Day"]] += abs(float(row["Calories Burned"]))
            for date, val in daily_burn.items():
                upsert_daily_nutrition(conn, date, {"Active Calories Burned": round(val, 1)})

    # Weight from biometrics (always check separately)
    if not conn.execute("SELECT 1 FROM daily_nutrition WHERE metric = 'Weight (lbs)' LIMIT 1").fetchone():
        bio_path = data_dir / "cronometer_biometrics.csv"
        if bio_path.exists():
            with open(bio_path) as f:
                for row in csv.DictReader(f):
                    if "Weight" in row.get("Metric", "") and "Apple Health" not in row.get("Metric", ""):
                        upsert_daily_nutrition(conn, row["Day"], {"Weight (lbs)": float(row["Amount"])})

    if not get_exercises(conn):
        hevy_path = data_dir / "hevy_workouts.csv"
        if hevy_path.exists():
            with open(hevy_path) as f:
                for row in csv.DictReader(f):
                    ex = row.get("exercise_title", "").strip()
                    w, r = row.get("weight_lbs", ""), row.get("reps", "")
                    st = row.get("start_time", "")
                    if not ex or not w or not r:
                        continue
                    try:
                        weight, reps = float(w), int(r)
                    except (ValueError, TypeError):
                        continue
                    date = _parse_hevy_date(st)
                    if not date:
                        continue
                    orm = _compute_orm(weight, reps)
                    if orm > 0:
                        upsert_lift_orm(conn, date, ex, round(orm, 1))

    requested = [m.strip() for m in metrics.split(",") if m.strip()]
    lookback = max(1, min(3, lookback))

    all_nutrition = get_nutrition_metrics(conn)
    all_exercises = get_exercises(conn)

    # Split requested into nutrition vs exercise vs biometrics
    nutrition_requested = [m for m in requested if m in all_nutrition]
    exercise_requested = [m for m in requested if m in all_exercises]
    biometrics_requested = [m for m in requested if m == "Weight (lbs)"]

    series = {}

    # Biometrics: raw, no rolling avg
    if biometrics_requested:
        series.update(query_nutrition(conn, biometrics_requested, 1))

    # Nutrition/burn: apply rolling avg via SQL
    if nutrition_requested:
        series.update(query_nutrition(conn, nutrition_requested, lookback))

    # Exercise ORM: raw, no rolling avg
    for ex in exercise_requested:
        orm_data = query_orm(conn, ex)
        series.update(orm_data)

    conn.close()

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
    from ..user_db import get_user_db, get_exercises, get_nutrition_metrics

    conn = get_user_db(user_data_dir(user_id))
    exercises = get_exercises(conn)
    nutrition_metrics = get_nutrition_metrics(conn)

    if not exercise:
        conn.close()
        return {"exercises": exercises, "nutrition_metrics": nutrition_metrics, "data": []}

    lookback = max(1, min(3, lookback))

    # Get ORM dates for this exercise
    orm_rows = conn.execute(
        "SELECT date, orm FROM lift_orm WHERE exercise = ? ORDER BY date", (exercise,)
    ).fetchall()

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
        placeholders = ",".join("?" * len(prior_dates))
        vals = conn.execute(
            f"SELECT value FROM daily_nutrition WHERE metric = ? AND date IN ({placeholders})",
            [nutrition_metric] + prior_dates
        ).fetchall()

        if vals:
            avg = round(sum(v["value"] for v in vals) / len(vals), 1)
            results.append({"date": lift_date, "orm": row["orm"], "avg_metric": avg})

    conn.close()
    return {
        "exercises": exercises,
        "nutrition_metrics": nutrition_metrics,
        "lookback": lookback,
        "metric": nutrition_metric,
        "exercise": exercise,
        "data": results,
    }
