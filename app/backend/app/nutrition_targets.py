"""
Nutrition targets service: DRI seeding, macro target resolution (fixed vs.
ratio mode), and per-nutrient progress computation.

Kept as a plain service module (not tied to any router) so this logic is
callable from multiple routes (targets.py, profile.py's post-save DRI
reseed, food.py's future progress hooks) without duplicating queries —
matches the "every capability is a real, independently-callable unit, not
frontend-embedded logic" requirement: the API-first constraint applies to
internal code organization too, not just the outward HTTP surface.
"""
import sys
from pathlib import Path
from typing import Optional

from .db import get_pool

# dri_reference.py lives at the backend root (sibling of app/), not inside
# the app package — same sys.path pattern already used by
# routers/data.py for `tdee` and routers/food.py for `integrations.*`.
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
from dri_reference import get_targets_for  # noqa: E402

KCAL_PER_GRAM = {"protein": 4, "carbs": 4, "fat": 9}


async def seed_dri_targets(user_id: int, sex: str, age: int) -> int:
    """
    Seed/refresh `nutrition_targets` for a user from the DRI reference
    table, for the given sex + age. Rows the user has explicitly
    customized (`is_custom = true`) are left untouched — this is what
    lets a profile edit (e.g. birthday passing into a new age bracket)
    refresh the *defaults* without silently overwriting a value the user
    deliberately chose.

    Returns the number of rows inserted/updated.
    """
    targets = get_targets_for(sex, age)
    pool = await get_pool()
    rows = [
        (user_id, name, info["unit"], info["daily_target"], info["max_threshold"])
        for name, info in targets.items()
    ]
    async with pool.acquire() as conn:
        await conn.executemany(
            """INSERT INTO nutrition_targets (user_id, nutrient_name, unit, daily_target, max_threshold, is_custom, updated_at)
               VALUES ($1, $2, $3, $4, $5, FALSE, now())
               ON CONFLICT (user_id, nutrient_name) DO UPDATE SET
                   unit = EXCLUDED.unit,
                   daily_target = EXCLUDED.daily_target,
                   max_threshold = EXCLUDED.max_threshold,
                   updated_at = now()
               WHERE nutrition_targets.is_custom = FALSE""",
            rows,
        )
    return len(rows)


def derive_macro_grams(
    mode: str,
    calorie_target: Optional[float] = None,
    protein_g: Optional[float] = None,
    carbs_g: Optional[float] = None,
    fat_g: Optional[float] = None,
    protein_pct: Optional[float] = None,
    carbs_pct: Optional[float] = None,
    fat_pct: Optional[float] = None,
) -> dict:
    """
    Resolve a macro_target_settings row into concrete gram targets.

    mode="fixed": returns the stored gram values as-is.
    mode="ratio": derives grams from calorie_target * pct / kcal_per_gram,
        so gram targets always stay in sync with the calorie target
        instead of being stored (and potentially going stale) directly.

    Raises ValueError for missing required fields per mode, or if ratio
    percentages don't sum to ~100 (allowing float rounding tolerance) —
    this is a genuine data-integrity check, not a UX nicety, since a
    ratio that doesn't sum to 100% silently produces a calorie target
    that doesn't match the macro grams it implies.
    """
    if mode == "fixed":
        if calorie_target is None or protein_g is None or carbs_g is None or fat_g is None:
            raise ValueError("fixed mode requires calorie_target, protein_g, carbs_g, fat_g")
        return {
            "calorie_target": calorie_target,
            "protein_g": protein_g,
            "carbs_g": carbs_g,
            "fat_g": fat_g,
        }

    if mode == "ratio":
        if calorie_target is None or protein_pct is None or carbs_pct is None or fat_pct is None:
            raise ValueError("ratio mode requires calorie_target, protein_pct, carbs_pct, fat_pct")
        total_pct = protein_pct + carbs_pct + fat_pct
        if abs(total_pct - 100) > 0.5:
            raise ValueError(f"macro percentages must sum to 100, got {total_pct}")
        return {
            "calorie_target": calorie_target,
            "protein_g": round(calorie_target * protein_pct / 100 / KCAL_PER_GRAM["protein"], 1),
            "carbs_g": round(calorie_target * carbs_pct / 100 / KCAL_PER_GRAM["carbs"], 1),
            "fat_g": round(calorie_target * fat_pct / 100 / KCAL_PER_GRAM["fat"], 1),
        }

    raise ValueError(f"unknown macro target mode: {mode!r}")


async def get_resolved_macro_targets(user_id: int) -> Optional[dict]:
    """Fetch a user's macro_target_settings row and resolve it to concrete
    gram targets via derive_macro_grams. Returns None if the user has never
    set macro targets (no row) rather than a fabricated default — callers
    decide what to do in that case (e.g. fall back to DRI protein RDA)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM macro_target_settings WHERE user_id = $1", user_id
        )
    if row is None:
        return None
    resolved = derive_macro_grams(
        mode=row["mode"],
        calorie_target=row["calorie_target"],
        protein_g=row["protein_g"],
        carbs_g=row["carbs_g"],
        fat_g=row["fat_g"],
        protein_pct=row["protein_pct"],
        carbs_pct=row["carbs_pct"],
        fat_pct=row["fat_pct"],
    )
    resolved["mode"] = row["mode"]
    return resolved


async def get_nutrient_progress(user_id: int, date: str) -> dict:
    """
    Resolved target-vs-actual for every tracked micronutrient, for the
    dashboard/progress-bar surface. Computed here (backend), not left for
    the frontend to assemble from raw rows — this is the concrete
    endpoint-shape decision the API-first constraint calls for.

    Returns {nutrient_name: {unit, daily_target, max_threshold, actual,
    percent_of_target}}. `percent_of_target` is None when daily_target is
    None (nutrient has no established DRI value) rather than a divide-by-
    zero or a fabricated 0%.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        target_rows = await conn.fetch(
            "SELECT nutrient_name, unit, daily_target, max_threshold FROM nutrition_targets WHERE user_id = $1",
            user_id,
        )
        actual_rows = await conn.fetch(
            """SELECT nf.nutrient_name, SUM(nf.value) AS total
               FROM nutrient_facts nf
               JOIN food_log fl ON fl.id = nf.owner_id AND nf.owner_type = 'food_log'
               WHERE fl.user_id = $1 AND fl.date = $2
               GROUP BY nf.nutrient_name""",
            user_id, date,
        )

    actuals = {r["nutrient_name"]: r["total"] for r in actual_rows}
    progress = {}
    for t in target_rows:
        actual = actuals.get(t["nutrient_name"], 0.0)
        daily_target = t["daily_target"]
        percent = round(actual / daily_target * 100, 1) if daily_target else None
        progress[t["nutrient_name"]] = {
            "unit": t["unit"],
            "daily_target": daily_target,
            "max_threshold": t["max_threshold"],
            "actual": actual,
            "percent_of_target": percent,
        }
    return progress
