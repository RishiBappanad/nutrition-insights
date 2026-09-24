"""Vitals API: log and read point-in-time body measurements (weight, body
fat). Readings are stored in daily_nutrition alongside every other
per-day metric (see vitals.py for why, and how Goals target them); this
router is just the generic entry point so a client doesn't need a
separate endpoint per vital -- POST /data/weight keeps working and lands
in the same place."""
from datetime import date as date_type, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .auth import get_current_user
from ..db.vitals import query as vitals_query
from ..user_db import upsert_daily_nutrition, upsert_tdee_log
from ..vitals import VITALS, is_valid_vital

router = APIRouter()


class VitalReading(BaseModel):
    metric: str  # a key of vitals.VITALS: "weight" | "body_fat"
    value: float
    date: Optional[str] = None  # YYYY-MM-DD; defaults to today (UTC)


@router.get("")
async def list_vitals():
    """The vitals a client can log, with their units."""
    return {"vitals": [{"metric": k, "label": v["label"], "unit": v["unit"]} for k, v in VITALS.items()]}


@router.post("", status_code=201)
async def log_vital(req: VitalReading, user_id: int = Depends(get_current_user)):
    if not is_valid_vital(req.metric):
        raise HTTPException(status_code=400, detail=f"metric must be one of: {', '.join(VITALS)}")
    if req.value <= 0 or (req.metric == "body_fat" and req.value >= 100):
        raise HTTPException(status_code=400, detail="value out of range for this vital")
    day = req.date or datetime.now(timezone.utc).date().isoformat()
    try:
        date_type.fromisoformat(day)
    except ValueError:
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")

    await upsert_daily_nutrition(user_id, day, {VITALS[req.metric]["metric"]: req.value})
    if req.metric == "weight":
        # Same second write POST /data/weight makes: BMR/TDEE's weight input.
        await upsert_tdee_log(user_id, day, weight_lbs=req.value)
    return {"metric": req.metric, "value": req.value, "date": day}


@router.get("/{metric}")
async def get_vital_history(metric: str, limit: int = 60, user_id: int = Depends(get_current_user)):
    """Newest-first readings for one vital."""
    if not is_valid_vital(metric):
        raise HTTPException(status_code=404, detail="Unknown vital")
    rows = await vitals_query.list_vital_readings(user_id, VITALS[metric]["metric"], max(1, min(limit, 500)))
    return {"metric": metric, "unit": VITALS[metric]["unit"], "readings": [{"date": r["date"], "value": r["value"]} for r in rows]}
