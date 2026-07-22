"""Exercise/activity diary — named activity entries (e.g. "Running, 30
min, 300 kcal"), matching Cronometer's "Exercise" diary tab. Distinct
from lift_orm (Hevy's structured strength-training sets: exercise/
weight/reps per set — a different domain, unstructured cardio/activity
vs. structured lifting) and tdee_log (one aggregate
active_calories_burned NUMBER per day with no per-activity breakdown at
all).

Every capability here is a real backend REST endpoint, independently
callable — no logic embedded only in frontend code, per this project's
explicit API-first requirement.

Writes go through food_entry_contract.py's ExerciseLogContract/
log_exercise_entry() (same decoupling pattern as food_log/recipes) so
this endpoint and the future Cronometer exercise sync share one write
implementation instead of the sync path reimplementing its own SQL."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..routers.auth import get_current_user
from ..db import get_pool
from ..food_entry_contract import ExerciseLogContract, log_exercise_entry

router = APIRouter()


class ExerciseLogRequest(BaseModel):
    date: str
    activity_name: str
    duration_minutes: Optional[float] = None
    calories_burned: float = 0
    notes: Optional[str] = None


class ExerciseLogUpdateRequest(BaseModel):
    date: Optional[str] = None
    activity_name: Optional[str] = None
    duration_minutes: Optional[float] = None
    calories_burned: Optional[float] = None
    notes: Optional[str] = None


def _row_to_entry(r) -> dict:
    return {
        "id": r["id"],
        "date": r["date"],
        "activity_name": r["activity_name"],
        "duration_minutes": r["duration_minutes"],
        "calories_burned": r["calories_burned"],
        "source": r["source"],
        "source_id": r["source_id"],
        "notes": r["notes"],
        "created_at": r["created_at"].isoformat(),
        "updated_at": r["updated_at"].isoformat(),
    }


@router.post("")
async def log_exercise(req: ExerciseLogRequest, user_id: int = Depends(get_current_user)):
    """Manually log one named activity entry. Builds an
    ExerciseLogContract and writes through log_exercise_entry() (see
    food_entry_contract.py) — the SAME write path the future Cronometer
    exercise sync will use, so this endpoint and that sync never diverge
    in validation/storage logic. `source` is always 'manual' here;
    synced-in Cronometer entries get source='Cronometer' + a real
    source_id from the sync path, never from this endpoint."""
    entry_id, _ = await log_exercise_entry(user_id, ExerciseLogContract(
        date=req.date,
        activity_name=req.activity_name,
        duration_minutes=req.duration_minutes,
        calories_burned=req.calories_burned,
        source="manual",
        notes=req.notes,
    ))
    return {"status": "logged", "id": entry_id}


@router.get("")
async def list_exercise_log(date: str = Query(...), user_id: int = Depends(get_current_user)):
    """All activity entries for one date, plus the day's total calories
    burned across all of them — the total is computed here (not left for
    the frontend to sum) per this project's convention of resolving
    aggregates server-side (see targets.py's /progress for the same
    pattern)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM exercise_log WHERE user_id = $1 AND date = $2 ORDER BY created_at",
            user_id, date,
        )
    entries = [_row_to_entry(r) for r in rows]
    return {
        "date": date,
        "entries": entries,
        "total_calories_burned": sum(e["calories_burned"] for e in entries),
    }


@router.patch("/{entry_id}")
async def update_exercise_entry(entry_id: int, req: ExerciseLogUpdateRequest, user_id: int = Depends(get_current_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT * FROM exercise_log WHERE id = $1 AND user_id = $2", entry_id, user_id
        )
        if existing is None:
            raise HTTPException(status_code=404, detail="Exercise entry not found")

        await conn.execute(
            """UPDATE exercise_log SET
                   date = COALESCE($1, date),
                   activity_name = COALESCE($2, activity_name),
                   duration_minutes = COALESCE($3, duration_minutes),
                   calories_burned = COALESCE($4, calories_burned),
                   notes = COALESCE($5, notes),
                   updated_at = now()
               WHERE id = $6 AND user_id = $7""",
            req.date, req.activity_name, req.duration_minutes, req.calories_burned, req.notes,
            entry_id, user_id,
        )
    return {"status": "updated"}


@router.delete("/{entry_id}")
async def delete_exercise_entry(entry_id: int, user_id: int = Depends(get_current_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM exercise_log WHERE id = $1 AND user_id = $2", entry_id, user_id
        )
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="Exercise entry not found")
    return {"status": "deleted"}
