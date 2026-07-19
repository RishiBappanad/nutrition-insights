"""Water tracking API: quick-add drinking-water log, sex-based default daily
goal, per-user override.

Per Cronometer's published defaults (cited in nutrition-diary-design.md):
48 fl oz (~1420 mL) for female, 64 fl oz (~1890 mL) for male. Stored
internally as mL always — unit display preference (cups/oz/mL) is a UI
concern, not persisted here."""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..routers.auth import get_current_user
from ..db import get_pool

router = APIRouter()

DEFAULT_WATER_TARGET_ML = {"female": 1420.0, "male": 1890.0}


class WaterLogRequest(BaseModel):
    date: str
    amount_ml: float


def default_water_target_ml(sex: str) -> float:
    """Resolve the sex-based default daily water goal. Falls back to the
    male default for any sex value other than 'female' rather than
    raising — matches this app's existing permissive-default convention
    (see dri_reference._age_bracket) and avoids hard-failing water-goal
    lookups for a profile that hasn't set sex yet."""
    return DEFAULT_WATER_TARGET_ML.get(sex.lower(), DEFAULT_WATER_TARGET_ML["male"])


@router.post("/log")
async def log_water(req: WaterLogRequest, user_id: int = Depends(get_current_user)):
    if req.amount_ml <= 0:
        raise HTTPException(status_code=400, detail="amount_ml must be positive")
    pool = await get_pool()
    async with pool.acquire() as conn:
        entry_id = await conn.fetchval(
            "INSERT INTO water_log (user_id, date, amount_ml) VALUES ($1, $2, $3) RETURNING id",
            user_id, req.date, req.amount_ml,
        )
    return {"status": "logged", "id": entry_id}


@router.get("/log")
async def get_water_log(date: str = Query(...), user_id: int = Depends(get_current_user)):
    """Entries for the day plus the total and resolved daily target
    (custom override if set on user_profile, else the sex-based default)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, amount_ml, logged_at FROM water_log WHERE user_id = $1 AND date = $2 ORDER BY logged_at",
            user_id, date,
        )
        profile = await conn.fetchrow(
            "SELECT sex, water_target_ml FROM user_profile WHERE user_id = $1", user_id
        )

    total_ml = sum(r["amount_ml"] for r in rows)
    if profile and profile["water_target_ml"] is not None:
        target_ml = profile["water_target_ml"]
    elif profile and profile["sex"]:
        target_ml = default_water_target_ml(profile["sex"])
    else:
        target_ml = None  # No profile yet — no default to resolve to.

    return {
        "date": date,
        "entries": [{"id": r["id"], "amount_ml": r["amount_ml"], "logged_at": r["logged_at"].isoformat()} for r in rows],
        "total_ml": total_ml,
        "target_ml": target_ml,
        "percent_of_target": round(total_ml / target_ml * 100, 1) if target_ml else None,
    }


@router.delete("/log/{entry_id}")
async def delete_water_entry(entry_id: int, user_id: int = Depends(get_current_user)):
    """Scoped to the current user, matching the existing food_log delete
    pattern — a user can never delete another user's entry even if they
    guess an id."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM water_log WHERE id = $1 AND user_id = $2", entry_id, user_id
        )
    return {"status": "deleted"}
