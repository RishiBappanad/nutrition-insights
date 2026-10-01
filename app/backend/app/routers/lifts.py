"""Manual strength-training log -- a deliberately minimal stand-in for
automatic lift-tracking sync (see archive/hevy_fitness_tracker/ for a
removed integration): nutrition and fitness are separate trackers per
CLAUDE.md's tenets, and TrackStack itself should merge them, not one
tracker's app owning another domain's sync integration.

This is intentionally not a full lift-tracking experience -- real
set-by-set strength tracking is deferred to a future, separate fitness
tracker. One logged weight x reps set in, one estimated 1RM out,
written straight into the lift_orm table, so Charts' Exercise tab and
Lift Insights work from manually-entered data the same as they would
from a synced source."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..routers.auth import get_current_user
from ..user_db import upsert_lift_orm, compute_orm

router = APIRouter()


class LiftLogRequest(BaseModel):
    date: str
    exercise: str
    weight_lbs: float
    reps: int


@router.post("/log")
async def log_lift(req: LiftLogRequest, user_id: int = Depends(get_current_user)):
    """Log one set; upsert_lift_orm keeps the best (highest) estimated
    1RM per (user, date, exercise) -- matches the exact semantics Hevy
    sync used, so logging multiple sets for the same lift on the same
    day just keeps whichever was hardest, same as before."""
    if not req.exercise.strip():
        raise HTTPException(status_code=400, detail="exercise is required")
    if req.weight_lbs <= 0 or req.reps <= 0:
        raise HTTPException(status_code=400, detail="weight_lbs and reps must be positive")
    orm = round(compute_orm(req.weight_lbs, req.reps), 1)
    await upsert_lift_orm(user_id, req.date, req.exercise.strip(), orm)
    return {"status": "logged", "orm": orm}
