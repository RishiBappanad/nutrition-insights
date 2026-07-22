"""Nutrition target settings API: macro targets (fixed/ratio), per-nutrient
micronutrient overrides, and resolved daily progress.

Every route here is a plain REST endpoint scoped by the authenticated
user_id — no logic in this feature is frontend-only, per the project's
explicit API-first requirement (see nutrition-diary-design.md)."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..routers.auth import get_current_user
from ..db import get_pool
from ..nutrition_targets import derive_macro_grams, get_nutrient_progress, get_resolved_macro_targets
from ..nutrient_groups import get_nutrient_groups

router = APIRouter()


class MacroTargetsRequest(BaseModel):
    mode: str  # "fixed" | "ratio"
    calorie_target: float
    protein_g: Optional[float] = None
    carbs_g: Optional[float] = None
    fat_g: Optional[float] = None
    protein_pct: Optional[float] = None
    carbs_pct: Optional[float] = None
    fat_pct: Optional[float] = None


class NutrientTargetOverride(BaseModel):
    nutrient_name: str
    daily_target: Optional[float] = None
    max_threshold: Optional[float] = None
    is_custom: bool = True


@router.get("/macros")
async def get_macro_targets(user_id: int = Depends(get_current_user)):
    """Return the user's macro targets, resolved to concrete grams
    regardless of stored mode. 404 if the user has never set any —
    callers (e.g. the dashboard) decide the no-target fallback behavior,
    this endpoint doesn't fabricate a default."""
    resolved = await get_resolved_macro_targets(user_id)
    if resolved is None:
        raise HTTPException(status_code=404, detail="No macro targets set")
    return resolved


@router.put("/macros")
async def set_macro_targets(req: MacroTargetsRequest, user_id: int = Depends(get_current_user)):
    """Set macro targets. Validates the request (via derive_macro_grams)
    before writing anything, so an invalid ratio (percentages not summing
    to 100) or missing required field never lands in the database."""
    try:
        resolved = derive_macro_grams(
            mode=req.mode,
            calorie_target=req.calorie_target,
            protein_g=req.protein_g,
            carbs_g=req.carbs_g,
            fat_g=req.fat_g,
            protein_pct=req.protein_pct,
            carbs_pct=req.carbs_pct,
            fat_pct=req.fat_pct,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO macro_target_settings
                   (user_id, mode, calorie_target, protein_g, carbs_g, fat_g, protein_pct, carbs_pct, fat_pct, updated_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, now())
               ON CONFLICT (user_id) DO UPDATE SET
                   mode = EXCLUDED.mode,
                   calorie_target = EXCLUDED.calorie_target,
                   protein_g = EXCLUDED.protein_g,
                   carbs_g = EXCLUDED.carbs_g,
                   fat_g = EXCLUDED.fat_g,
                   protein_pct = EXCLUDED.protein_pct,
                   carbs_pct = EXCLUDED.carbs_pct,
                   fat_pct = EXCLUDED.fat_pct,
                   updated_at = now()""",
            user_id, req.mode, req.calorie_target, req.protein_g, req.carbs_g, req.fat_g,
            req.protein_pct, req.carbs_pct, req.fat_pct,
        )
    resolved["mode"] = req.mode
    return resolved


@router.get("/nutrients")
async def get_nutrient_targets(user_id: int = Depends(get_current_user)):
    """List every tracked micronutrient's current target (DRI default or
    custom override, whichever is active) — the data source for the
    'Advanced' collapsed micronutrient settings section."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT nutrient_name, unit, daily_target, max_threshold, is_custom FROM nutrition_targets "
            "WHERE user_id = $1 ORDER BY nutrient_name",
            user_id,
        )
    return {
        "targets": [
            {
                "nutrient_name": r["nutrient_name"],
                "unit": r["unit"],
                "daily_target": r["daily_target"],
                "max_threshold": r["max_threshold"],
                "is_custom": r["is_custom"],
            }
            for r in rows
        ]
    }


@router.put("/nutrients/{nutrient_name}")
async def set_nutrient_target(
    nutrient_name: str,
    req: NutrientTargetOverride,
    user_id: int = Depends(get_current_user),
):
    """Set a per-nutrient custom override, or (is_custom=false) revert a
    nutrient back to tracking the DRI default. Requires the nutrient to
    already exist for this user (i.e. DRI seeding has run) — this isn't
    a general-purpose upsert for arbitrary nutrient names, since an
    override with no DRI counterpart has no unit/context to validate
    against."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT unit FROM nutrition_targets WHERE user_id = $1 AND nutrient_name = $2",
            user_id, nutrient_name,
        )
        if existing is None:
            raise HTTPException(
                status_code=404,
                detail=f"Nutrient {nutrient_name!r} not found for this user — run DRI seeding first",
            )
        await conn.execute(
            """UPDATE nutrition_targets SET daily_target = $1, max_threshold = $2, is_custom = $3, updated_at = now()
               WHERE user_id = $4 AND nutrient_name = $5""",
            req.daily_target, req.max_threshold, req.is_custom, user_id, nutrient_name,
        )
    return {"status": "updated"}


@router.get("/progress")
async def get_progress(date: str = Query(...), user_id: int = Depends(get_current_user)):
    """Resolved target-vs-actual for every tracked nutrient on a given
    date — the single source of truth for progress bars. Computed
    server-side (see nutrition_targets.get_nutrient_progress) so the
    frontend never re-derives percentages itself."""
    return {"date": date, "progress": await get_nutrient_progress(user_id, date)}


@router.get("/nutrient-groups")
async def get_nutrient_groups_endpoint():
    """Grouped micronutrient preset structure (Vitamins/Minerals groups,
    plus starter presets for the user-customizable 'important to me'
    card) — see app/nutrient_groups.py. A real, independently-callable
    endpoint (not frontend-hardcoded groups) so any client builds the
    same swipeable-card grouping without re-deriving it. Not user-scoped
    — the groupings themselves aren't personal data, only which
    nutrients a given user has pinned as "important to me" is (that's
    GET /preferences' important_nutrients field)."""
    return get_nutrient_groups()
