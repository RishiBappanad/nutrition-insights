import csv
import sys
import asyncio
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from ..routers.auth import get_current_user
from ..routers.data import user_data_dir
from ..db import get_pool, decrypt

router = APIRouter()


async def _get_user_creds(user_id: int) -> dict:
    pool = await get_pool()
    async with pool.acquire() as db:
        creds = await db.fetchrow("SELECT * FROM credentials WHERE user_id = $1", user_id)
    if not creds:
        raise HTTPException(status_code=400, detail="No credentials saved. Use /auth/credentials first.")
    return {
        "hevy_username": decrypt(creds["hevy_username"]) if creds["hevy_username"] else None,
        "hevy_password": decrypt(creds["hevy_password"]) if creds["hevy_password"] else None,
        "cronometer_username": decrypt(creds["cronometer_username"]) if creds["cronometer_username"] else None,
        "cronometer_password": decrypt(creds["cronometer_password"]) if creds["cronometer_password"] else None,
    }


def _filter_biometrics(csv_path: str) -> None:
    """Remove heart rate rows from biometrics CSV in place."""
    p = Path(csv_path)
    lines = p.read_text().splitlines()
    filtered = [lines[0]] + [l for l in lines[1:] if "Heart Rate" not in l]
    p.write_text("\n".join(filtered) + "\n")


def _parse_tdee_rows(cronometer_files: dict) -> dict:
    """Parse the same 3 Cronometer export files the old CSV-merge step
    always read, returning {date: {weight_lbs, calories_consumed,
    active_calories_burned}} — pure parsing, no I/O to any persistent
    store, so this is easy to point at Postgres instead of a CSV without
    touching the parsing logic itself."""
    from collections import defaultdict

    weights = {}
    bio_path = cronometer_files.get("biometrics")
    if bio_path:
        with open(bio_path) as f:
            for row in csv.DictReader(f):
                if "Weight" in row["Metric"] and "Apple Health" not in row["Metric"]:
                    weights[row["Day"]] = float(row["Amount"])

    calories_consumed = {}
    summary_path = cronometer_files.get("daily_summary")
    if summary_path:
        with open(summary_path) as f:
            for row in csv.DictReader(f):
                if row.get("Energy (kcal)"):
                    date = row.get("Date", "")
                    if date:
                        calories_consumed[date] = round(float(row["Energy (kcal)"]), 1)

    active_calories = defaultdict(float)
    exercises_path = cronometer_files.get("exercises")
    if exercises_path:
        with open(exercises_path) as f:
            for row in csv.DictReader(f):
                if row["Calories Burned"]:
                    active_calories[row["Day"]] += abs(float(row["Calories Burned"]))

    all_dates = set(weights) | set(calories_consumed) | set(active_calories.keys())
    return {
        date: {
            "weight_lbs": weights.get(date),
            "calories_consumed": calories_consumed.get(date),
            "active_calories_burned": round(active_calories[date], 1) if date in active_calories else None,
        }
        for date in all_dates
    }


async def _update_tdee_log(cronometer_files: dict, user_id: int) -> int:
    """Merge Cronometer exports into the tdee_log Postgres table (one
    upsert per date with new data) — previously merged into a local CSV
    file with no persistent volume behind it, so BMR was computed
    against whatever partial/reset history happened to survive on the
    specific Cloud Run instance handling the request. Returns the number
    of dates updated."""
    from ..user_db import upsert_tdee_log

    parsed = _parse_tdee_rows(cronometer_files)
    for date_str, fields in parsed.items():
        await upsert_tdee_log(user_id, date_str, **fields)
    return len(parsed)


@router.post("/cronometer")
async def sync_cronometer(user_id: int = Depends(get_current_user)):
    """
    Pull Cronometer data into this app — nutrition (daily_nutrition),
    biometrics/weight, and exercise-calorie-burn history (tdee_log).
    Pure pull, no side effects on the user's actual Cronometer account.

    Previously this endpoint also computed a BMR and pushed it back to
    Cronometer via client.set_bmr() as an automatic side effect of every
    sync — split out into a separate, explicit POST /sync/bmr action
    (see below) per the user's instruction that syncing should only
    *get* data from Cronometer, not silently write back to it. The
    underlying client already has set_bmr() as a real write-capable
    method (kept as-is, not removed) since a future explicit push
    feature is still wanted — this split just stops it from firing
    automatically and unconditionally on every pull.
    """
    creds = await _get_user_creds(user_id)
    if not creds["cronometer_username"] or not creds["cronometer_password"]:
        raise HTTPException(status_code=400, detail="Cronometer credentials not set")

    from integrations.cronometer_rpc import CronometerRPCClient
    from datetime import datetime

    data_dir = str(user_data_dir(user_id))

    try:
        client = CronometerRPCClient(creds["cronometer_username"], creds["cronometer_password"])
        client.login()
        results = client.export_all_to_files("2026-04-06", datetime.now().strftime("%Y-%m-%d"), output_dir=data_dir)

        if results.get("biometrics"):
            _filter_biometrics(results["biometrics"])
        tdee_days_updated = await _update_tdee_log(results, user_id)

        # Populate Postgres with nutrition data
        from ..user_db import upsert_daily_nutrition

        # Insert daily summary metrics
        if results.get("daily_summary"):
            with open(results["daily_summary"]) as f:
                for row in csv.DictReader(f):
                    if "Group" in row and row.get("Group", "").strip('"') != "Total":
                        continue
                    date = row.get("Date", "")
                    if not date:
                        continue
                    metrics = {}
                    for k, v in row.items():
                        if k in ("Date", "Group", "Completed") or not v:
                            continue
                        try:
                            metrics[k] = float(v)
                        except ValueError:
                            pass
                    if metrics:
                        await upsert_daily_nutrition(user_id, date, metrics)

        # Insert active calories burned as a nutrition metric
        if results.get("exercises"):
            from collections import defaultdict
            daily_burn = defaultdict(float)
            with open(results["exercises"]) as f:
                for row in csv.DictReader(f):
                    if row.get("Calories Burned"):
                        daily_burn[row["Day"]] += abs(float(row["Calories Burned"]))
            for date, val in daily_burn.items():
                await upsert_daily_nutrition(user_id, date, {"Active Calories Burned": round(val, 1)})

        # Insert weight as a nutrition metric (biometrics)
        if results.get("biometrics"):
            with open(results["biometrics"]) as f:
                for row in csv.DictReader(f):
                    if "Weight" in row["Metric"] and "Apple Health" not in row["Metric"]:
                        await upsert_daily_nutrition(user_id, row["Day"], {"Weight (lbs)": float(row["Amount"])})

        return {"status": "ok", "tdee_days_updated": tdee_days_updated}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/bmr")
async def sync_bmr(user_id: int = Depends(get_current_user), push_to_cronometer: bool = False):
    """
    Recalculate BMR from this user's tdee_log history (already pulled
    in via POST /sync/cronometer — this endpoint does NOT talk to
    Cronometer to fetch data, it only reads what's already stored).

    `push_to_cronometer=true` additionally writes the computed BMR back
    to the user's Cronometer account via the existing (write-capable)
    CronometerRPCClient.set_bmr() — opt-in, not automatic, and requires
    Cronometer credentials to be set even though this endpoint doesn't
    pull new data from Cronometer otherwise. Defaults to false: recalc
    without any push is the common case now that sync is pull-only.
    """
    from ..user_db import get_tdee_log as _get_tdee_log_rows
    from tdee import calculate_bmr

    # Check credentials before computing BMR, not after — a push request
    # with no credentials should fail clearly regardless of whether BMR
    # computation would have succeeded, rather than silently reporting
    # "pushed_to_cronometer: false" for two totally different reasons
    # (no credentials vs. no BMR to push) with the same 200 response.
    if push_to_cronometer:
        creds = await _get_user_creds(user_id)
        if not creds["cronometer_username"] or not creds["cronometer_password"]:
            raise HTTPException(status_code=400, detail="Cronometer credentials not set — required to push BMR")

    records = await _get_tdee_log_rows(user_id)
    bmr = calculate_bmr(records=records)

    if not isinstance(bmr, (int, float)):
        return {"status": "ok", "bmr": None, "message": str(bmr), "pushed_to_cronometer": False}

    pushed = False
    if push_to_cronometer:
        from integrations.cronometer_rpc import CronometerRPCClient
        try:
            client = CronometerRPCClient(creds["cronometer_username"], creds["cronometer_password"])
            client.login()
            client.set_bmr(int(bmr))
            pushed = True
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"BMR calculated ({bmr}) but push to Cronometer failed: {e}")

    return {"status": "ok", "bmr": bmr, "pushed_to_cronometer": pushed}


@router.post("/hevy")
async def sync_hevy(user_id: int = Depends(get_current_user)):
    """Export Hevy workout data via Playwright."""
    creds = await _get_user_creds(user_id)
    if not creds["hevy_username"] or not creds["hevy_password"]:
        raise HTTPException(status_code=400, detail="Hevy credentials not set")

    data_dir = str(user_data_dir(user_id))

    def _run_hevy():
        from integrations.hevy_web import HevyWebScraper
        with HevyWebScraper(headless=True) as scraper:
            if not scraper.login(creds["hevy_username"], creds["hevy_password"]):
                return None
            return scraper.export_workouts(output_dir=data_dir)

    try:
        loop = asyncio.get_event_loop()
        path = await loop.run_in_executor(None, _run_hevy)
        if not path:
            raise HTTPException(status_code=401, detail="Hevy login failed")

        # Populate lift_orm table from exported CSV
        from ..user_db import upsert_lift_orm
        from ..routers.data import _compute_orm, _parse_hevy_date

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


@router.post("/all")
async def sync_all(user_id: int = Depends(get_current_user)):
    """Run full sync pipeline."""
    results = {}
    try:
        crono = await sync_cronometer(user_id)
        results["cronometer"] = crono
    except Exception as e:
        results["cronometer"] = {"error": str(e)}

    try:
        hevy = await sync_hevy(user_id)
        results["hevy"] = hevy
    except Exception as e:
        results["hevy"] = {"error": str(e)}

    return results
