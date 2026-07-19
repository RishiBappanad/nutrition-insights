"""Pantry/fridge inventory API. See nutrition-diary-design.md "Pantry /
fridge schema" section for the full design rationale — this implements
that spec exactly: tracking_mode ('countable'|'bulk'|'single')
discriminates how POST /consume affects remaining_servings, rather than
three separate item types."""
import json
from datetime import date as date_cls, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..routers.auth import get_current_user
from ..db import get_pool
from ..routers.food import nutrients_to_rows

router = APIRouter()

VALID_MODES = {"countable", "bulk", "single"}


class PantryItemRequest(BaseModel):
    food_name: str
    source: Optional[str] = None
    source_id: Optional[str] = None
    serving_size: float = 1.0
    serving_unit: str = "serving"
    tracking_mode: str = "countable"
    remaining_servings: Optional[float] = None
    expiration_date: Optional[str] = None


class PantryItemUpdateRequest(BaseModel):
    remaining_servings: Optional[float] = None
    expiration_date: Optional[str] = None
    tracking_mode: Optional[str] = None


class ConsumeRequest(BaseModel):
    servings: float
    date: str
    meal: str = "Snack"
    # Nutrient/macro payload for the servings actually consumed — mirrors
    # food_log's POST body shape. The pantry item doesn't store its own
    # nutrition data (see design doc rationale), so the caller supplies it
    # at consume time, same as a normal food-log POST would.
    calories: float = 0
    protein: float = 0
    carbs: float = 0
    fat: float = 0
    fiber: float = 0
    nutrients: Optional[dict] = None


def _validate_mode(mode: str):
    if mode not in VALID_MODES:
        raise HTTPException(status_code=400, detail=f"tracking_mode must be one of {sorted(VALID_MODES)}")


@router.post("")
async def add_pantry_item(req: PantryItemRequest, user_id: int = Depends(get_current_user)):
    _validate_mode(req.tracking_mode)
    if req.tracking_mode == "countable" and req.remaining_servings is None:
        raise HTTPException(status_code=400, detail="countable items require remaining_servings")

    remaining = req.remaining_servings
    if req.tracking_mode == "single":
        remaining = 1.0  # single items are always exactly one, regardless of what was passed
    elif req.tracking_mode == "bulk":
        remaining = None  # bulk items don't track a count at all

    pool = await get_pool()
    async with pool.acquire() as conn:
        item_id = await conn.fetchval(
            """INSERT INTO pantry_items (user_id, food_name, source, source_id, serving_size,
                   serving_unit, tracking_mode, remaining_servings, expiration_date)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
               RETURNING id""",
            user_id, req.food_name, req.source, req.source_id, req.serving_size,
            req.serving_unit, req.tracking_mode, remaining, req.expiration_date,
        )
    return {"status": "added", "id": item_id}


@router.get("")
async def list_pantry_items(user_id: int = Depends(get_current_user)):
    """Excludes finished items — matches the design doc's 'gone means
    gone' behavior for bulk items marked finished."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM pantry_items WHERE user_id = $1 AND is_finished = FALSE ORDER BY expiration_date NULLS LAST, added_at",
            user_id,
        )
    return {"items": [_row_to_item(r) for r in rows]}


@router.get("/expiring")
async def get_expiring_items(days: int = Query(7, ge=0), user_id: int = Depends(get_current_user)):
    """Items expiring within `days` days (default 7), for a future
    dashboard/notification surface. Excludes items with no expiration
    date set — those never 'expire' by definition."""
    cutoff = (date_cls.today() + timedelta(days=days)).isoformat()
    today = date_cls.today().isoformat()
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT * FROM pantry_items
               WHERE user_id = $1 AND is_finished = FALSE
                 AND expiration_date IS NOT NULL
                 AND expiration_date <= $2
               ORDER BY expiration_date""",
            user_id, cutoff,
        )
    return {
        "items": [_row_to_item(r) for r in rows],
        "as_of": today,
        "within_days": days,
    }


@router.patch("/{item_id}")
async def update_pantry_item(item_id: int, req: PantryItemUpdateRequest, user_id: int = Depends(get_current_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT * FROM pantry_items WHERE id = $1 AND user_id = $2", item_id, user_id
        )
        if existing is None:
            raise HTTPException(status_code=404, detail="Pantry item not found")

        if req.tracking_mode is not None:
            _validate_mode(req.tracking_mode)

        await conn.execute(
            """UPDATE pantry_items SET
                   remaining_servings = COALESCE($1, remaining_servings),
                   expiration_date = COALESCE($2, expiration_date),
                   tracking_mode = COALESCE($3, tracking_mode),
                   updated_at = now()
               WHERE id = $4 AND user_id = $5""",
            req.remaining_servings, req.expiration_date, req.tracking_mode, item_id, user_id,
        )
    return {"status": "updated"}


@router.delete("/{item_id}")
async def delete_pantry_item(item_id: int, user_id: int = Depends(get_current_user)):
    """Remove without logging to diary (expired, thrown away, bought by
    mistake) — distinct from /consume, which logs first."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM pantry_items WHERE id = $1 AND user_id = $2", item_id, user_id
        )
    return {"status": "deleted"}


@router.post("/{item_id}/finish")
async def finish_pantry_item(item_id: int, user_id: int = Depends(get_current_user)):
    """Mark a bulk item as used up. Deletes the row outright (matches the
    design doc's 'gone means gone' reasoning — no soft-delete state to
    manage for a finished item)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM pantry_items WHERE id = $1 AND user_id = $2", item_id, user_id
        )
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="Pantry item not found")
    return {"status": "finished"}


@router.post("/{item_id}/consume")
async def consume_pantry_item(item_id: int, req: ConsumeRequest, user_id: int = Depends(get_current_user)):
    """
    Atomic pantry-to-diary action: creates a real food_log entry (+
    food_log_nutrients) for the consumed servings, then updates/removes
    the pantry item based on tracking_mode — one backend call, not two
    sequenced frontend requests. See design doc's "Consumption flow"
    section for the exact per-mode behavior:
      - countable: decrements remaining_servings; deletes the row if it
        would hit <= 0.
      - bulk: no quantity change (call /finish separately when it's used up).
      - single: any consumption deletes the row — there's no partial state.
    """
    if req.servings <= 0:
        raise HTTPException(status_code=400, detail="servings must be positive")

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            item = await conn.fetchrow(
                "SELECT * FROM pantry_items WHERE id = $1 AND user_id = $2 FOR UPDATE",
                item_id, user_id,
            )
            if item is None:
                raise HTTPException(status_code=404, detail="Pantry item not found")

            if item["tracking_mode"] == "countable":
                if item["remaining_servings"] is None or req.servings > item["remaining_servings"]:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Cannot consume {req.servings} servings — only "
                                f"{item['remaining_servings']} remaining",
                    )

            nutrients = req.nutrients or {}
            food_log_id = await conn.fetchval(
                """INSERT INTO food_log (user_id, date, meal, food_name, source, source_id,
                       serving_size, serving_unit, calories, protein, carbs, fat, fiber, nutrients_json)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
                   RETURNING id""",
                user_id, req.date, req.meal, item["food_name"], item["source"], item["source_id"],
                req.servings, item["serving_unit"], req.calories, req.protein, req.carbs, req.fat, req.fiber,
                json.dumps(nutrients),
            )
            rows = nutrients_to_rows(food_log_id, nutrients)
            if rows:
                await conn.executemany(
                    """INSERT INTO food_log_nutrients (food_log_id, nutrient_name, value, unit)
                       VALUES ($1, $2, $3, $4)""",
                    rows,
                )

            pantry_status = "unchanged"
            if item["tracking_mode"] == "single":
                await conn.execute("DELETE FROM pantry_items WHERE id = $1", item_id)
                pantry_status = "removed"
            elif item["tracking_mode"] == "countable":
                new_remaining = item["remaining_servings"] - req.servings
                if new_remaining <= 0:
                    await conn.execute("DELETE FROM pantry_items WHERE id = $1", item_id)
                    pantry_status = "removed"
                else:
                    await conn.execute(
                        "UPDATE pantry_items SET remaining_servings = $1, updated_at = now() WHERE id = $2",
                        new_remaining, item_id,
                    )
                    pantry_status = "decremented"

    return {"status": "logged", "food_log_id": food_log_id, "pantry_status": pantry_status}


def _row_to_item(r) -> dict:
    return {
        "id": r["id"],
        "food_name": r["food_name"],
        "source": r["source"],
        "source_id": r["source_id"],
        "serving_size": r["serving_size"],
        "serving_unit": r["serving_unit"],
        "tracking_mode": r["tracking_mode"],
        "remaining_servings": r["remaining_servings"],
        "is_finished": r["is_finished"],
        "expiration_date": r["expiration_date"],
        "added_at": r["added_at"].isoformat(),
        "updated_at": r["updated_at"].isoformat(),
    }
