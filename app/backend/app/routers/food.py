"""Food search and logging API routes."""
import json
from fastapi import APIRouter, Depends, Query
from ..routers.auth import get_current_user
from ..db import get_pool

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
        "nutrients": {...}
    }
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO food_log (user_id, date, meal, food_name, source, source_id,
               serving_size, serving_unit, calories, protein, carbs, fat, fiber, nutrients_json)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)""",
            user_id,
            entry.get("date"),
            entry.get("meal", "Snack"),
            entry.get("food_name"),
            entry.get("source"),
            entry.get("source_id"),
            entry.get("serving_size", 1.0),
            entry.get("serving_unit", "serving"),
            entry.get("calories", 0),
            entry.get("protein", 0),
            entry.get("carbs", 0),
            entry.get("fat", 0),
            entry.get("fiber", 0),
            json.dumps(entry.get("nutrients", {})),
        )
    return {"status": "logged"}


@router.get("/log")
async def get_food_log(
    date: str = Query(...),
    user_id: int = Depends(get_current_user),
):
    """Get all food entries for a given date."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM food_log WHERE user_id = $1 AND date = $2 ORDER BY id",
            user_id, date,
        )

    entries = []
    for r in rows:
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
        })

    totals = {
        "calories": sum(e["calories"] for e in entries),
        "protein": sum(e["protein"] for e in entries),
        "carbs": sum(e["carbs"] for e in entries),
        "fat": sum(e["fat"] for e in entries),
        "fiber": sum(e["fiber"] for e in entries),
    }

    return {"entries": entries, "totals": totals}


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
