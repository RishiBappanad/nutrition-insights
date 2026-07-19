"""Food search and logging API routes."""
import json
from fastapi import APIRouter, Depends, HTTPException, Query
from ..routers.auth import get_current_user
from ..db import get_pool
from ..portion_scaling import scale_food_entry

router = APIRouter()


@router.get("/search")
async def search_food(
    q: str = Query(..., min_length=2),
    sources: str = Query("USDA,CNF"),
    user_id: int = Depends(get_current_user),
):
    """Search foods across USDA and CNF databases."""
    from integrations.food_search import search_foods

    source_list = [s.strip() for s in sources.split(",")]
    results = search_foods(q, source_list)
    return {"results": results}


@router.post("/log")
async def log_food(
    entry: dict,
    user_id: int = Depends(get_current_user),
):
    """
    Log a food entry. Body:
    {
        "date": "2026-06-22",
        "meal": "Lunch",
        "food_name": "Bananas, raw",
        "source": "USDA",
        "source_id": "173944",
        "serving_size": 1.0,
        "serving_unit": "medium (118g)",
        "calories": 105,
        "protein": 1.3,
        "carbs": 27,
        "fat": 0.4,
        "fiber": 3.1,
        "nutrients": {"Sodium, Na": {"value": 1.2, "unit": "mg"}, ...},
        "scale_to": {"mode": "grams", "from_grams": 118, "to_grams": 250}
    }

    `nutrients` (if present) is persisted structurally into
    food_log_nutrients (one row per nutrient), not just kept as an
    unread JSON blob — this is what lets /nutrition/progress compute
    per-nutrient daily totals with SQL instead of parsing JSON per row.
    Entries without a `calories`/`protein`/etc. key in `nutrients` still
    get those 5 macro columns populated from the top-level fields for
    backwards compatibility with the existing dashboard totals.

    `scale_to` is optional. If present, the backend scales `calories`/
    `protein`/`carbs`/`fat`/`fiber`/`nutrients` by the requested amount
    before storing — the caller sends the food's reference (unscaled)
    values plus the target amount, not pre-scaled numbers, so the actual
    multiplication happens in one place (portion_scaling.py) instead of
    being reimplemented by every caller (or, before this existed, not
    implemented at all). If `scale_to` is omitted, the request body's
    top-level fields are stored exactly as given — unchanged behavior for
    existing callers.
      - mode="grams": {"from_grams": 118, "to_grams": 250} — for foods
        with a real gram-based reference (USDA/CNF's serving_size, when
        it's a weight).
      - mode="multiple": {"servings_requested": 2} — for foods with no
        gram reference (e.g. "1 jar"), just N of the reference serving.
    """
    pool = await get_pool()
    nutrients: dict = entry.get("nutrients") or {}
    macros = {
        "calories": entry.get("calories", 0),
        "protein": entry.get("protein", 0),
        "carbs": entry.get("carbs", 0),
        "fat": entry.get("fat", 0),
        "fiber": entry.get("fiber", 0),
    }

    scale_to = entry.get("scale_to")
    if scale_to:
        try:
            scaled = scale_food_entry(
                macros=macros,
                nutrients=nutrients,
                mode=scale_to.get("mode"),
                from_grams=scale_to.get("from_grams"),
                to_grams=scale_to.get("to_grams"),
                servings_requested=scale_to.get("servings_requested"),
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        macros = scaled["macros"]
        nutrients = scaled["nutrients"]

    async with pool.acquire() as conn:
        async with conn.transaction():
            food_log_id = await conn.fetchval(
                """INSERT INTO food_log (user_id, date, meal, food_name, source, source_id,
                   serving_size, serving_unit, calories, protein, carbs, fat, fiber, nutrients_json)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
                   RETURNING id""",
                user_id,
                entry.get("date"),
                entry.get("meal", "Snack"),
                entry.get("food_name"),
                entry.get("source"),
                entry.get("source_id"),
                entry.get("serving_size", 1.0),
                entry.get("serving_unit", "serving"),
                macros["calories"],
                macros["protein"],
                macros["carbs"],
                macros["fat"],
                macros["fiber"],
                json.dumps(nutrients),
            )

            rows = nutrients_to_rows(food_log_id, nutrients)
            if rows:
                await conn.executemany(
                    """INSERT INTO food_log_nutrients (food_log_id, nutrient_name, value, unit)
                       VALUES ($1, $2, $3, $4)
                       ON CONFLICT (food_log_id, nutrient_name) DO UPDATE SET value = EXCLUDED.value, unit = EXCLUDED.unit""",
                    rows,
                )

    return {"status": "logged", "id": food_log_id}


def nutrients_to_rows(food_log_id: int, nutrients: dict) -> list[tuple]:
    """Normalize the request body's `nutrients` dict into
    (food_log_id, nutrient_name, value, unit) rows for food_log_nutrients.
    Skips entries missing a numeric value rather than raising, since food
    search results can legitimately have gaps in nutrient coverage per
    food item (USDA/CNF don't guarantee every nutrient is reported for
    every food).

    Shared utility — also used by routers/pantry.py's consume flow, which
    needs the identical nutrient-normalization logic when it creates a
    food_log entry on the caller's behalf."""
    rows = []
    for name, info in nutrients.items():
        if not isinstance(info, dict):
            continue
        value = info.get("value")
        if value is None:
            continue
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        rows.append((food_log_id, name, value, info.get("unit", "")))
    return rows


@router.get("/log")
async def get_food_log(
    date: str = Query(...),
    user_id: int = Depends(get_current_user),
):
    """Get all food entries for a given date, including each entry's full
    per-nutrient breakdown (from food_log_nutrients) and day-level totals
    for every nutrient that appears on at least one entry — not just the
    5 hardcoded macro columns."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM food_log WHERE user_id = $1 AND date = $2 ORDER BY id",
            user_id, date,
        )
        entry_ids = [r["id"] for r in rows]
        nutrient_rows = []
        if entry_ids:
            nutrient_rows = await conn.fetch(
                "SELECT food_log_id, nutrient_name, value, unit FROM food_log_nutrients "
                "WHERE food_log_id = ANY($1::int[])",
                entry_ids,
            )

    nutrients_by_entry: dict[int, dict] = {}
    for nr in nutrient_rows:
        nutrients_by_entry.setdefault(nr["food_log_id"], {})[nr["nutrient_name"]] = {
            "value": nr["value"],
            "unit": nr["unit"],
        }

    entries = []
    nutrient_totals: dict[str, dict] = {}
    for r in rows:
        entry_nutrients = nutrients_by_entry.get(r["id"], {})
        entries.append({
            "id": r["id"],
            "date": r["date"],
            "meal": r["meal"],
            "food_name": r["food_name"],
            "source": r["source"],
            "serving_size": r["serving_size"],
            "serving_unit": r["serving_unit"],
            "calories": r["calories"],
            "protein": r["protein"],
            "carbs": r["carbs"],
            "fat": r["fat"],
            "fiber": r["fiber"],
            "nutrients": entry_nutrients,
        })
        for name, info in entry_nutrients.items():
            bucket = nutrient_totals.setdefault(name, {"value": 0.0, "unit": info["unit"]})
            bucket["value"] += info["value"]

    totals = {
        "calories": sum(e["calories"] for e in entries),
        "protein": sum(e["protein"] for e in entries),
        "carbs": sum(e["carbs"] for e in entries),
        "fat": sum(e["fat"] for e in entries),
        "fiber": sum(e["fiber"] for e in entries),
    }

    return {"entries": entries, "totals": totals, "nutrient_totals": nutrient_totals}


@router.delete("/log/{entry_id}")
async def delete_food_entry(
    entry_id: int,
    user_id: int = Depends(get_current_user),
):
    """Delete a food log entry. Scoped to the current user so one user cannot
    delete another user's entry by guessing an id."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM food_log WHERE id = $1 AND user_id = $2", entry_id, user_id
        )
    return {"status": "deleted"}
