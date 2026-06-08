import sys
import asyncio
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from ..routers.auth import get_current_user
from ..routers.data import user_data_dir
from ..db import get_db, decrypt

router = APIRouter()


async def _get_user_creds(user_id: int) -> dict:
    db = await get_db()
    row = await db.execute("SELECT * FROM credentials WHERE user_id = ?", (user_id,))
    creds = await row.fetchone()
    await db.close()
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


def _update_tdee_log(cronometer_files: dict, output_path: str) -> None:
    """Update tdee_tracking_log.csv from cronometer exports."""
    import csv
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
    if not all_dates:
        return

    existing = {}
    try:
        with open(output_path) as f:
            for row in csv.DictReader(f):
                existing[row["Date"]] = row
    except FileNotFoundError:
        pass

    for date_str in all_dates:
        if date_str not in existing:
            existing[date_str] = {"Date": date_str, "Weight_lbs": "", "Calories_Consumed": "", "Active_Calories_Burned": ""}
        if date_str in weights:
            existing[date_str]["Weight_lbs"] = weights[date_str]
        if date_str in calories_consumed:
            existing[date_str]["Calories_Consumed"] = calories_consumed[date_str]
        if date_str in active_calories:
            existing[date_str]["Active_Calories_Burned"] = round(active_calories[date_str], 1)

    rows = sorted(existing.values(), key=lambda r: r["Date"])
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["Date", "Weight_lbs", "Calories_Consumed", "Active_Calories_Burned"])
        writer.writeheader()
        writer.writerows(rows)


@router.post("/cronometer")
async def sync_cronometer(user_id: int = Depends(get_current_user)):
    """Export Cronometer data and calculate BMR."""
    creds = await _get_user_creds(user_id)
    if not creds["cronometer_username"] or not creds["cronometer_password"]:
        raise HTTPException(status_code=400, detail="Cronometer credentials not set")

    from integrations.cronometer_rpc import CronometerRPCClient
    from tdee import calculate_bmr
    from datetime import datetime

    data_dir = str(user_data_dir(user_id))
    tdee_path = str(user_data_dir(user_id) / "tdee_tracking_log.csv")

    try:
        client = CronometerRPCClient(creds["cronometer_username"], creds["cronometer_password"])
        client.login()
        results = client.export_all_to_files("2026-04-06", datetime.now().strftime("%Y-%m-%d"), output_dir=data_dir)

        if results.get("biometrics"):
            _filter_biometrics(results["biometrics"])
        _update_tdee_log(results, tdee_path)

        # Populate SQLite with nutrition data
        from ..user_db import get_user_db, upsert_daily_nutrition
        conn = get_user_db(user_data_dir(user_id))

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
                        upsert_daily_nutrition(conn, date, metrics)

        # Insert active calories burned as a nutrition metric
        if results.get("exercises"):
            from collections import defaultdict
            daily_burn = defaultdict(float)
            with open(results["exercises"]) as f:
                for row in csv.DictReader(f):
                    if row.get("Calories Burned"):
                        daily_burn[row["Day"]] += abs(float(row["Calories Burned"]))
            for date, val in daily_burn.items():
                upsert_daily_nutrition(conn, date, {"Active Calories Burned": round(val, 1)})

        # Insert weight as a nutrition metric (biometrics)
        if results.get("biometrics"):
            with open(results["biometrics"]) as f:
                for row in csv.DictReader(f):
                    if "Weight" in row["Metric"] and "Apple Health" not in row["Metric"]:
                        upsert_daily_nutrition(conn, row["Day"], {"Weight (lbs)": float(row["Amount"])})

        conn.close()

        bmr = calculate_bmr(tdee_path)
        if isinstance(bmr, (int, float)):
            client.set_bmr(int(bmr))
            return {"status": "ok", "bmr": bmr}

        return {"status": "ok", "bmr": None, "message": str(bmr)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
        from ..user_db import get_user_db, upsert_lift_orm
        from ..routers.data import _compute_orm, _parse_hevy_date
        conn = get_user_db(user_data_dir(user_id))

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
                    upsert_lift_orm(conn, date, exercise, round(orm, 1))

        conn.close()
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
