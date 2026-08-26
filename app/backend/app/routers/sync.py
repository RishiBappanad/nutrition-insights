import csv
import sys
import asyncio
import logging
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from ..routers.auth import get_current_user
from ..routers.data import user_data_dir
from ..db import get_pool, decrypt

logger = logging.getLogger(__name__)

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


# Cronometer's "servings" export IS the diary — one row per logged food/
# recipe entry, already fetched on every sync via export_all_to_files but
# never parsed until now (confirmed via search: parse_servings_csv existed
# in cronometer_rpc.py but had zero callers anywhere in this codebase).
# This is a materially simpler and more reliable path to diary data than
# decoding raw GWT-RPC responses (getDayInfo/getAllFood) — it's a stable,
# already-authenticated CSV export, not something requiring further
# reverse-engineering.
#
# Column name -> (food_log column, is_macro) for the 4 hardcoded macro
# fields; every other numeric column in the CSV (including "Fiber (g)")
# becomes a food_log_nutrients row instead, under its raw CSV column name
# — same as every other Cronometer-sourced nutrient (fiber is not treated
# specially here; it's not a macro field on FoodLogEntryContract).
# Cronometer's CSV header uses "Âµg" for micrograms (a UTF-8/Latin-1
# mojibake artifact in their own export, not something this code
# introduces) — matched literally since that's what real exports contain,
# confirmed against an actual downloaded file.
_SERVINGS_MACRO_COLUMNS = {
    "Energy (kcal)": "calories",
    "Protein (g)": "protein",
    "Carbs (g)": "carbs",
    "Fat (g)": "fat",
}
# Columns that are metadata, not nutrients — skipped when building the
# food_log_nutrients rows so they don't show up as bogus "nutrients."
_SERVINGS_NON_NUTRIENT_COLUMNS = {"Day", "Group", "Food Name", "Amount", "Category"}

_MEAL_GROUP_MAP = {
    "breakfast": "Breakfast", "lunch": "Lunch", "dinner": "Dinner",
    "snack": "Snack", "snacks": "Snack",  # Cronometer's own export uses the plural "Snacks" -- confirmed against a real 898-row export
}


def _parse_amount(amount_str: str) -> tuple[float, str]:
    """Split Cronometer's "Amount" column (e.g. "12.00 nugget", "8.00 fl
    oz", "1.00 x 11.0 fl oz") into (numeric serving_size, serving_unit
    text) -- food_log.serving_size is DOUBLE PRECISION, so the raw string
    can't be stored there directly. Confirmed real formats against the
    898-row sample export: always starts with a decimal number, followed
    by a space and a free-text unit description (which may itself contain
    spaces/further numbers, e.g. "1.00 x 11.0 fl oz" -- only the FIRST
    number is the serving_size, the rest of the string is the unit label
    verbatim, not further parsed)."""
    amount_str = (amount_str or "").strip()
    if not amount_str:
        return 1.0, "serving"
    parts = amount_str.split(None, 1)
    try:
        size = float(parts[0])
    except ValueError:
        return 1.0, amount_str
    unit = parts[1] if len(parts) > 1 else "serving"
    return size, unit


def _servings_row_to_food_log_entry(row: dict) -> "FoodLogEntryContract":
    """Convert one row of Cronometer's servings CSV into a
    FoodLogEntryContract (see app/food_entry_contract.py) — the same
    canonical shape POST /food/log's request body uses. This is the
    decoupling point: this function's only job is "Cronometer CSV row ->
    the shared contract," nothing here knows about SQL or table
    structure. The actual write happens via
    food_entry_contract.log_food_entry(), called from
    _sync_diary_entries() below — not from here, and not via any direct
    INSERT in this module."""
    from ..food_entry_contract import FoodLogEntryContract

    macros = {}
    for csv_col, macro_key in _SERVINGS_MACRO_COLUMNS.items():
        val = row.get(csv_col)
        try:
            macros[macro_key] = float(val) if val not in (None, "") else 0.0
        except ValueError:
            macros[macro_key] = 0.0

    nutrients = {}
    for col, val in row.items():
        if col in _SERVINGS_NON_NUTRIENT_COLUMNS or col in _SERVINGS_MACRO_COLUMNS:
            continue
        if val in (None, ""):
            continue
        try:
            value = float(val)
        except ValueError:
            continue
        # Column names are like "B1 (Thiamine) (mg)" -- the unit is the
        # last parenthesized segment; store it alongside the value so
        # this matches the {name: {value, unit}} shape used everywhere
        # else nutrients are stored, not a bare number with no unit.
        unit = ""
        if col.endswith(")") and "(" in col:
            unit = col[col.rindex("(") + 1:-1]
        nutrients[col] = {"value": value, "unit": unit}

    meal = _MEAL_GROUP_MAP.get((row.get("Group") or "").strip().lower(), row.get("Group") or "Uncategorized")
    serving_size, serving_unit = _parse_amount(row.get("Amount", ""))

    return FoodLogEntryContract(
        date=row.get("Day", ""),
        meal=meal,
        food_name=row.get("Food Name", "").strip() or "Unknown",
        source="Cronometer",
        source_id=None,  # Cronometer's servings export has no stable per-serving ID
        serving_size=serving_size,
        serving_unit=serving_unit,
        nutrients=nutrients,
        **macros,
    )


async def _sync_exercise_entries(cronometer_files: dict, user_id: int) -> int:
    """
    Parse the exercises export, convert each row to an
    ExerciseLogContract (app/food_entry_contract.py), and write it via
    log_exercise_entry() — the SAME shared write path POST /exercise
    uses. Same decoupling rationale as _sync_diary_entries.

    UNLIKE _sync_diary_entries, this IS duplicate-safe on resync: the
    exercises CSV has no stable per-row id either, but
    log_exercise_entry() dedupes on (user_id, source, source_id) when
    source_id is present -- so this builds a deterministic composite
    source_id (date + activity_name + duration + calories) per row via
    _exercise_row_source_id() below. Re-syncing the same historical range
    re-derives the SAME source_id for an unchanged row and dedupes
    correctly, while two genuinely different real entries on the same
    day (different activity, or the same activity logged twice with
    different duration/calories) still get distinct source_ids and both
    import. This deliberately does NOT repeat the known duplicate-import
    limitation _sync_diary_entries still has -- exercise sync was built
    after that gap was already identified, so it avoids the same mistake
    from the start rather than inheriting it.

    Returns the number of entries imported (dedup-skipped rows are not
    counted as newly imported).
    """
    from ..food_entry_contract import log_exercise_entry
    from integrations.cronometer_rpc import parse_exercises_csv

    exercises_path = cronometer_files.get("exercises")
    if not exercises_path:
        return 0

    with open(exercises_path) as f:
        raw_csv = f.read()

    try:
        rows = parse_exercises_csv(raw_csv)
    except ValueError:
        # A real schema mismatch (see parse_exercises_csv's own
        # validation) -- surfaced to the caller as "0 imported" rather
        # than crashing the whole /sync/cronometer call, since exercise
        # sync failing shouldn't block the food diary / BMR / biometrics
        # parts of the same sync from completing. The underlying error
        # is still logged server-side for investigation.
        import logging
        logging.getLogger(__name__).exception("Failed to parse exercises CSV during sync")
        return 0

    count = 0
    for row in rows:
        entry = _exercise_row_to_contract(row)
        if entry is None:
            continue
        _entry_id, was_created = await log_exercise_entry(user_id, entry)
        if was_created:
            count += 1
    return count


def _exercise_row_source_id(date: str, activity_name: str, duration_minutes, calories_burned) -> str:
    """Deterministic composite key for one exercises-CSV row -- same
    inputs always produce the same source_id, so log_exercise_entry()'s
    dedupe (see its own docstring) correctly recognizes a re-synced row
    as already-imported. Isolated as its own function so this logic is
    directly unit-testable without needing a DB connection or an
    async/event-loop context (see tests/test_exercise_sync_parsing.py)."""
    import hashlib
    composite = f"{date}|{activity_name}|{duration_minutes}|{calories_burned}"
    return hashlib.sha256(composite.encode()).hexdigest()[:16]


def _exercise_row_to_contract(row: dict):
    """Convert one parsed exercises-CSV row (see parse_exercises_csv) into
    an ExerciseLogContract, or None if the row is missing required
    fields (date/activity_name) and should be skipped. Isolated from
    _sync_exercise_entries so this pure conversion logic is directly
    unit-testable without a DB connection."""
    from ..food_entry_contract import ExerciseLogContract

    if not row.get("date") or not row.get("activity_name"):
        return None
    source_id = _exercise_row_source_id(
        row["date"], row["activity_name"], row.get("duration_minutes"), row.get("calories_burned"),
    )
    return ExerciseLogContract(
        date=str(row["date"]),
        activity_name=str(row["activity_name"]),
        duration_minutes=row.get("duration_minutes"),
        calories_burned=float(row.get("calories_burned") or 0),
        source="Cronometer",
        source_id=source_id,
    )


async def _sync_diary_entries(cronometer_files: dict, user_id: int) -> int:
    """
    Parse the servings export, convert each row to a FoodLogEntryContract
    (app/food_entry_contract.py), and write it via log_food_entry() — the
    SAME shared write path POST /food/log uses. This module has zero
    direct SQL against food_log/food_log_nutrients: parsing (CSV row ->
    contract) and storage (contract -> database) are fully decoupled, so
    a future change to how entries are validated/stored applies here
    automatically, and a future import source only needs to produce a
    FoodLogEntryContract, not learn the schema.

    Duplicate-safe by construction: every sync re-parses the FULL export
    range (export_all_to_files always re-exports from a fixed start date,
    not incrementally) and re-inserts. This means a repeated sync
    currently creates duplicate food_log rows for entries already
    imported on a prior sync -- a real, known limitation, not a silent
    bug: there's no natural unique key to dedupe against (Cronometer's
    export has no per-serving ID, only day+food+amount, which isn't
    reliably unique if you log the exact same food/amount twice in one
    day on purpose). Deduping this properly would need either an explicit
    "only import entries newer than last sync" date-based cutoff, or
    accepting the small risk of legitimate double-entries not being
    distinguishable from re-sync duplicates -- flagging as a known
    follow-up, not fixing silently by picking one of those tradeoffs here.

    Returns the number of entries imported.
    """
    from ..food_entry_contract import log_food_entry

    servings_path = cronometer_files.get("servings")
    if not servings_path:
        return 0

    with open(servings_path) as f:
        rows = list(csv.DictReader(f))

    count = 0
    for raw_row in rows:
        entry = _servings_row_to_food_log_entry(raw_row)
        if not entry.date:
            continue
        await log_food_entry(user_id, entry)
        count += 1
    return count


@router.post("/cronometer")
async def sync_cronometer(user_id: int = Depends(get_current_user)):
    """
    Pull Cronometer data into this app — nutrition (daily_nutrition),
    biometrics/weight, exercise-calorie-burn history (tdee_log), AND
    diary entries (food_log/food_log_nutrients, source='Cronometer') from
    the servings export. Pure pull, no side effects on the user's actual
    Cronometer account.

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
    from integrations.cronometer_web import CronometerWebScraper
    from datetime import datetime

    data_dir = str(user_data_dir(user_id))

    try:
        client = CronometerRPCClient(creds["cronometer_username"], creds["cronometer_password"])
        client.login()
        results = client.export_all_to_files("2026-04-06", datetime.now().strftime("%Y-%m-%d"), output_dir=data_dir)
    except Exception as rpc_error:
        # If RPC export fails (e.g., 403), try web scraper as fallback
        logger.warning(f"Cronometer RPC export failed: {rpc_error}. Trying web scraper fallback...")
        try:
            with CronometerWebScraper(headless=True) as scraper:
                if scraper.login(creds["cronometer_username"], creds["cronometer_password"]):
                    results = scraper.export_all("2026-04-06", datetime.now().strftime("%Y-%m-%d"), output_dir=data_dir)
                else:
                    raise HTTPException(status_code=401, detail="Cronometer web login failed")
        except Exception as web_error:
            logger.error(f"Cronometer web scraper also failed: {web_error}")
            raise HTTPException(status_code=502, detail=f"Cronometer export unavailable (RPC: {rpc_error}; Web: {web_error})")

        if results.get("biometrics"):
            _filter_biometrics(results["biometrics"])
        tdee_days_updated = await _update_tdee_log(results, user_id)
        diary_entries_imported = await _sync_diary_entries(results, user_id)
        exercise_entries_imported = await _sync_exercise_entries(results, user_id)

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

        return {
            "status": "ok",
            "tdee_days_updated": tdee_days_updated,
            "diary_entries_imported": diary_entries_imported,
            "exercise_entries_imported": exercise_entries_imported,
        }
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


@router.get("/cronometer/recipes")
async def list_cronometer_recipes(user_id: int = Depends(get_current_user)):
    """List candidate custom recipes from Cronometer for import into TrackStack, with is_imported status."""
    creds = await _get_user_creds(user_id)
    if not creds["cronometer_username"] or not creds["cronometer_password"]:
        raise HTTPException(status_code=400, detail="Cronometer credentials not set")

    from integrations.cronometer_rpc import CronometerRPCClient
    from ..db import get_pool

    try:
        # Get existing imported recipe source_ids for this user
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT source_id FROM recipes WHERE user_id = $1 AND source = 'Cronometer'", user_id)
            imported_set = {r["source_id"] for r in rows if r["source_id"]}

        client = CronometerRPCClient(creds["cronometer_username"], creds["cronometer_password"])
        client.login()
        raw_gwt = client.list_my_foods()
        foods = client.parse_find_my_foods(raw_gwt)
        
        candidates = []
        for item in foods:
            food_id = item.get("food_id")
            name = item.get("name")
            if not food_id or not name:
                continue
            try:
                food_detail = client.get_food(food_id)
                if food_detail and food_detail.get("is_recipe"):
                    ing_count = len(food_detail.get("ingredients", []))
                    if ing_count >= 1:
                        candidates.append({
                            "food_id": food_id,
                            "name": name,
                            "ingredient_count": ing_count,
                            "is_imported": str(food_id) in imported_set,
                        })
            except Exception:
                pass
        return {"status": "ok", "recipes": candidates}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cronometer/recipes/{food_id}/import")
async def import_cronometer_recipe(food_id: int, user_id: int = Depends(get_current_user)):
    """Resolve a single Cronometer recipe's ingredients and import into TrackStack."""
    creds = await _get_user_creds(user_id)
    if not creds["cronometer_username"] or not creds["cronometer_password"]:
        raise HTTPException(status_code=400, detail="Cronometer credentials not set")

    from integrations.cronometer_rpc import CronometerRPCClient
    from ..food_entry_contract import RecipeImportContract, RecipeItemContract, import_recipe

    try:
        client = CronometerRPCClient(creds["cronometer_username"], creds["cronometer_password"])
        client.login()
        recipe_info = client.get_food(food_id)
        if not recipe_info:
            raise HTTPException(status_code=404, detail="Recipe not found on Cronometer")

        recipe_name = recipe_info.get("name") or f"Cronometer Recipe #{food_id}"
        ingredients = recipe_info.get("ingredients", [])

        items = []
        for ing in ingredients:
            ing_id = ing["food_id"]
            amount_g = ing.get("amount_grams", 100.0)
            try:
                ing_info = client.get_food(ing_id)
                ing_name = ing_info.get("name") or f"Ingredient #{ing_id}"
                nutrients = ing_info.get("nutrients", {})
                scale = amount_g / 100.0 if amount_g else 1.0

                cal = round(nutrients.get("calories", 0) * scale, 1)
                prot = round(nutrients.get("protein", 0) * scale, 1)
                carb = round(nutrients.get("carbs", 0) * scale, 1)
                fat = round(nutrients.get("fat", 0) * scale, 1)

                # Fiber is not a macro field on RecipeItemContract — it's
                # folded into `nutrients` under "Fiber, total dietary"
                # (the same canonical name used everywhere else in the
                # app), like every other non-macro nutrient.
                item_nutrients = {
                    k: {"value": round(v * scale, 2), "unit": "G"}
                    for k, v in nutrients.items() if k not in ("calories", "protein", "carbs", "fat", "fiber")
                }
                if "fiber" in nutrients:
                    item_nutrients["Fiber, total dietary"] = {
                        "value": round(nutrients["fiber"] * scale, 2), "unit": "G",
                    }

                items.append(RecipeItemContract(
                    food_name=ing_name,
                    source="Cronometer",
                    source_id=str(ing_id),
                    amount_grams=amount_g,
                    amount_multiple=1.0,
                    calories=cal,
                    protein=prot,
                    carbs=carb,
                    fat=fat,
                    nutrients=item_nutrients,
                ))
            except Exception:
                items.append(RecipeItemContract(
                    food_name=f"Ingredient #{ing_id}",
                    source="Cronometer",
                    source_id=str(ing_id),
                    amount_grams=amount_g,
                ))

        contract = RecipeImportContract(
            name=recipe_name,
            servings_per_batch=1.0,
            source="Cronometer",
            source_id=str(food_id),
            items=items,
        )

        recipe_id = await import_recipe(user_id, contract)
        return {
            "status": "ok",
            "recipe_id": recipe_id,
            "name": recipe_name,
            "ingredients_imported": len(items),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

