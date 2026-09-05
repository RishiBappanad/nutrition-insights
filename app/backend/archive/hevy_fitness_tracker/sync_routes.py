"""Archived reference copy of the Hevy sync route and its supporting
read endpoints, as they existed in nutrition-insights immediately before
removal on 2026-09-05. See README.md in this folder for why and what
replaced this. NOT wired into any app -- not imported from anywhere,
kept only as a working starting point for a future port.

Original locations:
  - sync_hevy(), _run_hevy(): app/routers/sync.py
  - get_workouts(), get_orm(), _get_orm_data(), _parse_hevy_date():
    app/routers/data.py
  - _compute_orm(): app/routers/data.py (this one was NOT removed --
    it's generic 1RM math, moved to app/user_db.py as compute_orm() and
    still used by the new POST /lifts/log endpoint. Reproduced here
    unchanged so this file is self-contained for porting.)

To resume this in a new app: recreate a `credentials` table with
hevy_username/hevy_password columns (encrypted, see app/db.py's
encrypt()/decrypt()), a `lift_orm` table (user_id, date, exercise, orm),
and wire these functions into that app's own router + auth dependency.
"""
import csv
import asyncio
from pathlib import Path


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
    """Process a Hevy workouts CSV export into {exercise: [{date, orm}]}
    with the max ORM per day. Superseded in nutrition-insights by
    querying the lift_orm Postgres table directly (query_orm() in
    user_db.py) -- this CSV-file-based version depended on Cloud Run's
    ephemeral local filesystem still holding the last sync's export,
    which doesn't survive a container restart."""
    from collections import defaultdict

    if not csv_path.exists():
        return {}

    daily_max = defaultdict(lambda: defaultdict(float))

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

    result = {}
    for exercise, dates in daily_max.items():
        result[exercise] = sorted(
            [{"date": d, "orm": v} for d, v in dates.items()],
            key=lambda x: x["date"]
        )
    return result


# --- Original app/routers/data.py endpoints (dead CSV-file reads even
# before this archival -- query_orm() against Postgres was the real path
# by the time this was archived) ---------------------------------------

async def get_workouts_ROUTE(user_data_dir, user_id: int, limit: int = 20):
    """Was: GET /data/workouts"""
    csv_path = user_data_dir(user_id) / "hevy_workouts.csv"
    if not csv_path.exists():
        return {"workouts": []}
    with open(csv_path) as f:
        rows = list(csv.DictReader(f))
    return {"workouts": rows[-limit:]}


async def get_orm_ROUTE(user_data_dir, user_id: int):
    """Was: GET /data/orm"""
    csv_path = user_data_dir(user_id) / "hevy_workouts.csv"
    return _get_orm_data(csv_path)


# --- Original app/routers/sync.py endpoint ------------------------------

async def sync_hevy_ROUTE(user_id: int, get_user_creds, user_data_dir):
    """Was: POST /sync/hevy. `get_user_creds` and `user_data_dir` are the
    original app's helper functions, passed in here so this file has no
    hard dependency on nutrition-insights' module layout -- a port
    should replace them with the new app's own equivalents."""
    from fastapi import HTTPException
    from .hevy_web import HevyWebScraper

    creds = await get_user_creds(user_id)
    if not creds["hevy_username"] or not creds["hevy_password"]:
        raise HTTPException(status_code=400, detail="Hevy credentials not set")

    data_dir = str(user_data_dir(user_id))

    def _run_hevy():
        with HevyWebScraper(headless=True) as scraper:
            if not scraper.login(creds["hevy_username"], creds["hevy_password"]):
                return None
            return scraper.export_workouts(output_dir=data_dir)

    try:
        loop = asyncio.get_event_loop()
        path = await loop.run_in_executor(None, _run_hevy)
        if not path:
            raise HTTPException(status_code=401, detail="Hevy login failed")

        from .user_db_upsert_lift_orm import upsert_lift_orm  # placeholder import -- point at the new app's own user_db

        with open(path) as f:
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
                if orm > 0:
                    await upsert_lift_orm(user_id, date, exercise, round(orm, 1))

        return {"status": "ok", "file": path}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
