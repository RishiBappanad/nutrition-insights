"""
Nutrition targets service: DRI seeding, macro target resolution, and
per-nutrient progress computation -- all backed by the `goals` table since
the 2026-09-24 Targets/Goals unification (see db/targets/query.py for the
source-column conventions, and db/__init__.py for the one-time migration
that moved the old nutrition_targets/macro_target_settings tables here).
A "target" is just a daily goal: a nutrient's RDA floor is a gte goal, its
UL ceiling an lte goal, both measuring `nutrient:<Name>` over food_log
consumption.

Kept as a plain service module (not tied to any router) so this logic is
callable from multiple routes (targets.py, profile.py's post-save DRI
reseed, food.py's future progress hooks) without duplicating queries --
matches the "every capability is a real, independently-callable unit, not
frontend-embedded logic" requirement: the API-first constraint applies to
internal code organization too, not just the outward HTTP surface.
"""
import sys
from pathlib import Path
from typing import Optional

from .db import get_pool
from .db.targets import query as targets_query
from .db.profile import query as profile_query
from .goal_query import compute_nutrient_totals_for_date

# dri_reference.py lives at the backend root (sibling of app/), not inside
# the app package -- same sys.path pattern already used by
# routers/data.py for `tdee` and routers/food.py for `integrations.*`.
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
from dri_reference import DRI_TABLE, get_targets_for  # noqa: E402

KCAL_PER_GRAM = {"protein": 4, "carbs": 4, "fat": 9}


def daily_target_query(measure_field: Optional[str] = None) -> dict:
    """The measure_query every target-style goal uses: today's sum over
    food_log entries only (pantry stock, recipes, and custom foods carry
    calories/nutrient_facts too but aren't consumption), optionally over a
    nutrient instead of calories."""
    query = {
        "aggregation": "sum",
        "filters": [{"field": "owner_type", "operator": "eq", "value": "food_log"}],
        "timeWindow": {"kind": "current_period", "period": "daily"},
    }
    if measure_field:
        query["measureField"] = measure_field
    return query


# (goal label, measureField, comparator, tolerance_percent, resolved-dict key)
# Protein is a floor ("at least X g" is how it's almost always framed);
# calories and the other two macros are a target band. A judgment call made
# at migration time -- the old macro_target_settings had no comparator at
# all, its progress bars just showed % of target either direction.
MACRO_GOALS = (
    ("Calories", None, "within_tolerance_percent", 10, "calorie_target"),
    ("Protein", "nutrient:Protein", "gte", None, "protein_g"),
    ("Carbs", "nutrient:Carbohydrate, by difference", "within_tolerance_percent", 10, "carbs_g"),
    ("Fat", "nutrient:Total lipid (fat)", "within_tolerance_percent", 10, "fat_g"),
)

_COMPARATOR_FIELD = {"gte": "daily_target", "lte": "max_threshold"}


# Carbohydrate/fat are macros the DRI table has no RDA row for, but goals
# and macro targets measure them all the same.
_EXTRA_NUTRIENT_UNITS = {"Carbohydrate, by difference": "g", "Total lipid (fat)": "g"}


def nutrient_unit(nutrient_name: str) -> str:
    info = DRI_TABLE.get(nutrient_name)
    return info["unit"] if info else _EXTRA_NUTRIENT_UNITS.get(nutrient_name, "")


async def seed_dri_targets(user_id: int, sex: str, age: int) -> int:
    """
    Seed/refresh a user's DRI nutrient targets (goals with source
    'dri_default') for the given sex + age. Goals the user has customized
    (source 'user_target') are left untouched -- this is what lets a
    profile edit (e.g. a birthday passing into a new age bracket) refresh
    the *defaults* without silently overwriting a value the user
    deliberately chose. A default whose UL no longer exists for the new
    bracket is removed, matching the old behavior of overwriting
    max_threshold with NULL.

    Returns the number of nutrients seeded.
    """
    targets = get_targets_for(sex, age)
    existing = {(r["nutrient_name"], r["comparator"]): r for r in await targets_query.list_nutrient_target_goals(user_id)}

    inserts, updates, deletes = [], [], []
    for name, info in targets.items():
        for comparator, field in _COMPARATOR_FIELD.items():
            value = info[field]
            current = existing.get((name, comparator))
            if value is None:
                if current is not None and current["source"] == "dri_default":
                    deletes.append(current["id"])
            elif current is None:
                label = name if comparator == "gte" else f"{name} (max)"
                inserts.append((label, comparator, daily_target_query(f"nutrient:{name}"), value, "dri_default"))
            elif current["source"] == "dri_default" and current["reference_amount"] != value:
                updates.append((current["id"], value, "dri_default"))

    await targets_query.apply_nutrient_goal_changes(user_id, inserts, updates, deletes)
    return len(targets)


async def list_nutrient_targets(user_id: int) -> list[dict]:
    """Every nutrient with a target, folded back to the one-row-per-nutrient
    shape the settings UI and the old API used: daily_target from the
    nutrient's floor goal, max_threshold from its ceiling goal, is_custom
    when either was user-customized."""
    grouped: dict[str, dict] = {}
    for r in await targets_query.list_nutrient_target_goals(user_id):
        entry = grouped.setdefault(r["nutrient_name"], {
            "nutrient_name": r["nutrient_name"], "unit": nutrient_unit(r["nutrient_name"]),
            "daily_target": None, "max_threshold": None, "is_custom": False,
        })
        entry[_COMPARATOR_FIELD[r["comparator"]]] = r["reference_amount"]
        if r["source"] == "user_target":
            entry["is_custom"] = True
    return sorted(grouped.values(), key=lambda e: e["nutrient_name"])


async def set_nutrient_override(user_id: int, nutrient_name: str, daily_target: Optional[float], max_threshold: Optional[float]) -> bool:
    """Customize a nutrient's floor/ceiling. A None value removes that
    bound. Returns False if the nutrient has no target for this user yet
    (same contract as before: this isn't a general upsert for arbitrary
    nutrient names, since a target with no DRI counterpart has no unit or
    context to validate against)."""
    existing = {r["comparator"]: r for r in await targets_query.list_nutrient_target_goals(user_id) if r["nutrient_name"] == nutrient_name}
    if not existing:
        return False

    inserts, updates, deletes = [], [], []
    for comparator, value in (("gte", daily_target), ("lte", max_threshold)):
        current = existing.get(comparator)
        if value is None:
            if current is not None:
                deletes.append(current["id"])
        elif current is not None:
            updates.append((current["id"], value, "user_target"))
        else:
            label = nutrient_name if comparator == "gte" else f"{nutrient_name} (max)"
            inserts.append((label, comparator, daily_target_query(f"nutrient:{nutrient_name}"), value, "user_target"))

    await targets_query.apply_nutrient_goal_changes(user_id, inserts, updates, deletes)
    return True


async def revert_nutrient_to_dri(user_id: int, nutrient_name: str) -> bool:
    """Drop a nutrient's customizations and restore its DRI defaults right
    away (the old flow just flipped a flag and waited for the next profile
    save to re-seed). Returns False if there's no profile to derive the
    defaults from."""
    profile = await profile_query.get_profile(user_id)
    if profile is None:
        return False
    rows = await targets_query.list_nutrient_target_goals(user_id)
    deletes = [r["id"] for r in rows if r["nutrient_name"] == nutrient_name and r["source"] == "user_target"]
    await targets_query.apply_nutrient_goal_changes(user_id, [], [], deletes)
    await seed_dri_targets(user_id, profile["sex"], profile["age"])
    return True


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
    Resolve a macro target request into concrete gram targets.

    mode="fixed": returns the given gram values as-is.
    mode="ratio": derives grams from calorie_target * pct / kcal_per_gram,
        so gram targets always stay in sync with the calorie target
        instead of being supplied (and potentially going stale) directly.

    Raises ValueError for missing required fields per mode, or if ratio
    percentages don't sum to ~100 (allowing float rounding tolerance) --
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


async def set_macro_targets(user_id: int, resolved: dict) -> None:
    """Persist resolved macro targets (already run through
    derive_macro_grams) as four goals. Ratio mode is resolved to concrete
    grams at write time and stored as fixed values -- the settings UI only
    ever round-tripped fixed grams anyway, so nothing that used to work
    stops working; the one behavior given up is grams auto-following a
    later calorie-target edit without re-entering the ratio."""
    definitions = [
        (label, comparator, tolerance, daily_target_query(measure_field), resolved[key])
        for label, measure_field, comparator, tolerance, key in MACRO_GOALS
    ]
    await targets_query.set_macro_goals(user_id, definitions)


async def get_resolved_macro_targets(user_id: int) -> Optional[dict]:
    """The user's macro targets as concrete grams, or None if they've never
    set any -- callers decide what to do in that case (e.g. fall back to
    DRI protein RDA) instead of this fabricating a default. `mode` is
    always "fixed" now (see set_macro_targets)."""
    amounts = await targets_query.get_macro_goal_amounts(user_id)
    if not amounts:
        return None
    resolved = {key: amounts.get(label) for label, _f, _c, _t, key in MACRO_GOALS}
    resolved["mode"] = "fixed"
    return resolved


async def get_nutrient_progress(user_id: int, date: str) -> dict:
    """
    Resolved target-vs-actual for every tracked nutrient on a specific
    date, for the dashboard/progress-bar surface. Computed here (backend),
    not left for the frontend to assemble -- the concrete endpoint-shape
    decision the API-first constraint calls for.

    Returns {nutrient_name: {unit, daily_target, max_threshold, actual,
    percent_of_target}}. `percent_of_target` is None when there's no
    daily_target rather than a divide-by-zero or a fabricated 0%.

    "Actual" is read through the goal query layer's deduplicated view of
    domain_events (an edited entry counts once, a deleted one not at all),
    for the requested date specifically -- not "the current period"
    relative to now -- since the dashboard navigates to arbitrary days.
    """
    targets = await list_nutrient_targets(user_id)
    pool = await get_pool()
    async with pool.acquire() as conn:
        actuals = await compute_nutrient_totals_for_date(conn, user_id, date)

    progress = {}
    for t in targets:
        actual = actuals.get(t["nutrient_name"], 0.0)
        daily_target = t["daily_target"]
        progress[t["nutrient_name"]] = {
            "unit": t["unit"],
            "daily_target": daily_target,
            "max_threshold": t["max_threshold"],
            "actual": actual,
            "percent_of_target": round(actual / daily_target * 100, 1) if daily_target else None,
        }
    return progress
