"""
Goals API -- Python/FastAPI port of finance-tracker's routes/goals.ts.
See workspace-notes/RECURRING_AND_GOALS_SPEC.md for the full cross-
tracker design; goal_query.py/goals_evaluation.py are this tracker's own
copies of the shared interpreter/evaluator finance-tracker's lib/db
already implements.

Goals here also carry the nutrition "targets": DRI defaults and macro
targets are goals with a `source` (see db/targets/query.py), shown in the
same list as presets -- editable (amount), resettable to their default,
never deleted. Every goal is asserted on a nutrient total (organized as
Macros / Vitamins / Minerals / Other) or on a vital (weight, body fat),
with its standard unit; goal_measures.py is the catalog.

Two goals that would assert the same thing (two daily protein floors) are
rejected with a 409 naming the existing one -- see goal_signature.py.

`inflation_adjusted` is accepted and stored for schema parity across
trackers, but goals_evaluation.py's compute_goal_status never actually
adjusts anything with it here (see that module's own note) -- CPI
inflation is a money concept finance-tracker's Goals uses, not a
nutrition one.
"""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, Union

from .auth import get_current_user
from ..db.goals import query as goals_query
from ..db.goals.query import DuplicateGoalError
from ..db.profile import query as profile_query
from ..db.targets import query as targets_query
from ..db import get_pool
from ..goal_query import (
    parse_goal_query, is_valid_period, is_valid_measure_field, GOAL_QUERY_PERIODS, goal_query_to_json,
    compute_nutrient_totals_for_date,
)
from ..goals_evaluation import (
    is_valid_comparator, is_valid_severity, compute_goal_status, is_compliant, percent_of_reference,
)
from ..goal_measures import list_measures, describe_measure, GROUP_ORDER
from ..goal_signature import goal_signature, signature_of_goal
from ..nutrition_targets import get_targets_for, seed_dri_targets
from ..vitals import VITALS, VITAL_OWNER_TYPE, is_valid_vital

router = APIRouter()

# A goal is a "preset" when the system created it (a DRI default, the
# customized version of one, or a macro target) rather than the user
# authoring it from scratch.
PRESET_SOURCES = ("dri_default", "user_target", "macro_target")
# What a user may change on a preset: how much, not what it measures. A
# different shape is a goal of their own (and the duplicate check keeps it
# from shadowing the preset).
PRESET_EDITABLE_FIELDS = ("reference_amount", "tolerance_percent", "notify_on_crossing", "label")


class GoalInput(BaseModel):
    # Shorthand fields (basic goal). `category` (a food category) and
    # `measure_field` ("nutrient:<Name>", omitted = calories) are both
    # optional narrowing choices; target_amount + period are what make it
    # shorthand.
    category: Optional[str] = None
    measure_field: Optional[str] = None
    # A vital's key ("weight" | "body_fat") instead of a nutrient: the goal
    # is asserted on the latest reading, so no `period` is needed.
    vital: Optional[str] = None
    target_amount: Optional[float] = None
    period: Optional[str] = None
    # Full-form fields (advanced goal)
    measure_query: Optional[dict] = None
    reference_amount: Optional[float] = None
    reference_query: Optional[dict] = None
    # Shared fields
    comparator: str
    tolerance_percent: Optional[float] = None
    severity: str = "target"
    label: Optional[str] = None
    inflation_adjusted: bool = False
    notify_on_crossing: bool = True


class GoalUpdateInput(BaseModel):
    label: Optional[str] = None
    severity: Optional[str] = None
    is_active: Optional[bool] = None
    comparator: Optional[str] = None
    tolerance_percent: Optional[float] = None
    inflation_adjusted: Optional[bool] = None
    notify_on_crossing: Optional[bool] = None
    category: Optional[str] = None
    measure_field: Optional[str] = None
    vital: Optional[str] = None
    target_amount: Optional[float] = None
    period: Optional[str] = None
    measure_query: Optional[dict] = None
    reference_amount: Optional[float] = None
    reference_query: Optional[dict] = None
    # Edits just the multiplier on a computed reference (the ratio) without
    # re-sending the whole query.
    reference_scale: Optional[float] = None


def _is_shorthand(body: GoalInput) -> bool:
    return body.target_amount is not None and (body.period is not None or body.vital is not None) and body.measure_query is None


_CONSUMPTION_FILTER = {"field": "owner_type", "operator": "eq", "value": "food_log"}


def _ensure_consumption_filter(query: dict) -> dict:
    """A nutrition goal measures what was EATEN. domain_events here also
    carries pantry stock, recipes, meals, custom foods, and exercise --
    all with `amount`/nutrient_facts of their own -- so an unfiltered
    query would count a pantry restock, or calories burned, toward a
    calorie goal. Added server-side (unless the caller already filters on
    owner_type themselves -- a vital goal does) so no client has to
    remember it."""
    if any(f.get("field") == "owner_type" for f in query["filters"]):
        return query
    return {**query, "filters": [*query["filters"], dict(_CONSUMPTION_FILTER)]}


def vital_measure_query(vital: str) -> dict:
    """A vital goal reads the latest reading, ever: "what is my weight
    now", not a sum or an average over a period that may hold no reading."""
    return {
        "aggregation": "last",
        "filters": [
            {"field": "owner_type", "operator": "eq", "value": VITAL_OWNER_TYPE},
            {"field": "category", "operator": "eq", "value": vital},
        ],
        "timeWindow": {"kind": "all_time"},
    }


def _expand_shorthand(category: Optional[str], measure_field: Optional[str], vital: Optional[str], target_amount: float, period: Optional[str]) -> tuple[dict, float]:
    """Expands the flat shorthand into the same stored shape the full
    form uses -- see RECURRING_AND_GOALS_SPEC.md's "Basic goals stay
    simple" section. A basic goal never requires hand-building the full
    query JSON."""
    if vital:
        return vital_measure_query(vital), target_amount
    filters = [dict(_CONSUMPTION_FILTER)]
    if category:
        filters.append({"field": "category", "operator": "eq", "value": category})
    measure_query = {
        "aggregation": "sum",
        "filters": filters,
        "timeWindow": {"kind": "current_period", "period": period},
    }
    if measure_field:
        measure_query["measureField"] = measure_field
    return measure_query, target_amount


def _preset_defaults(profile) -> dict:
    """{(nutrient_name, comparator): default amount} for this user's
    profile -- the RDA floor (gte) and UL ceiling (lte) the presets are
    seeded from. Empty until a profile (age + sex) exists."""
    if profile is None:
        return {}
    defaults = {}
    for name, info in get_targets_for(profile["sex"], profile["age"]).items():
        if info["daily_target"] is not None:
            defaults[(name, "gte")] = info["daily_target"]
        if info["max_threshold"] is not None:
            defaults[(name, "lte")] = info["max_threshold"]
    return defaults


def _default_amount_for(goal: dict, defaults: dict) -> Optional[float]:
    """The preset default for a goal, when it has one: a preset-sourced,
    plain floor/ceiling on a nutrient with a DRI value."""
    field = goal["measure_query"].get("measureField")
    if goal.get("source") not in PRESET_SOURCES or not field or goal["reference_query"] is not None:
        return None
    return defaults.get((field[len("nutrient:"):], goal["comparator"]))


def _serialize_goal(g: dict, defaults: Optional[dict] = None) -> dict:
    measure = describe_measure(g["measure_query"])
    default_amount = _default_amount_for(g, defaults or {})
    return {
        "id": g["id"],
        "label": g["label"],
        "severity": g["severity"],
        "is_active": g["is_active"],
        "comparator": g["comparator"],
        "tolerance_percent": g["tolerance_percent"],
        "measure_query": g["measure_query"],
        "reference_amount": g["reference_amount"],
        "reference_query": g["reference_query"],
        # For a goal compared against a computed value: what that value
        # measures (so "protein vs. calories" can say so) and the multiplier
        # applied to it -- the ratio.
        "reference_measure": describe_measure(g["reference_query"]) if g["reference_query"] else None,
        "reference_scale": (g["reference_query"] or {}).get("scale", 1) if g["reference_query"] else None,
        "inflation_adjusted": g["inflation_adjusted"],
        "notify_on_crossing": g["notify_on_crossing"],
        "source": g.get("source"),
        # What the goal measures, for grouping/display: the nutrient group
        # (Macros/Vitamins/Minerals/Other) or Vitals, its label, and its
        # standard unit.
        "group": measure["group"],
        "measure_label": measure["label"],
        "vital": measure["vital"],
        "unit": measure["unit"],
        "is_preset": g.get("source") in PRESET_SOURCES,
        "preset_amount": default_amount,
        "is_modified": default_amount is not None and g["reference_amount"] != default_amount,
        "created_at": g["created_at"].isoformat(),
        "updated_at": g["updated_at"].isoformat(),
    }


async def _serialize(g: dict, user_id: int) -> dict:
    return _serialize_goal(g, _preset_defaults(await profile_query.get_profile(user_id)))


def _duplicate_conflict(e: DuplicateGoalError) -> HTTPException:
    existing = e.existing
    name = existing["label"] or describe_measure(existing["measure_query"])["label"]
    return HTTPException(status_code=409, detail={
        "message": f'You already have this goal ("{name}") -- edit it instead of adding a second one.',
        "existing_goal_id": existing["id"],
    })


def _parse_goal_input(body: Union[GoalInput, GoalUpdateInput]) -> tuple[Optional[dict], Optional[float], Optional[dict], Optional[str]]:
    """Returns (measure_query, reference_amount, reference_query, error).
    Shared by create and update: accepts either shorthand
    (category/vital/target_amount/period) or full-form (measure_query/
    reference_amount/reference_query) input."""
    if any(v is not None for v in (body.category, body.measure_field, body.vital, body.target_amount, body.period)):
        if body.measure_query is not None:
            return None, None, None, "cannot mix shorthand (category/measure_field/vital/target_amount/period) with measure_query"
        if body.vital is not None:
            if not is_valid_vital(body.vital):
                return None, None, None, f"vital must be one of: {', '.join(VITALS)}"
            if body.measure_field is not None or body.category is not None:
                return None, None, None, "a vital goal can't also name a nutrient or food category"
            if body.target_amount is None:
                return None, None, None, "shorthand requires target_amount"
        else:
            if body.target_amount is None or body.period is None:
                return None, None, None, "shorthand requires target_amount and period"
            if not is_valid_period(body.period):
                return None, None, None, f"period must be one of: {', '.join(GOAL_QUERY_PERIODS)}"
            if body.measure_field is not None and not is_valid_measure_field(body.measure_field):
                return None, None, None, 'measure_field must be "nutrient:<Name>" or omitted'
        measure_query, reference_amount = _expand_shorthand(body.category, body.measure_field, body.vital, body.target_amount, body.period)
        return measure_query, reference_amount, None, None

    if body.measure_query is None:
        return None, None, None, None  # no query edit requested (update-only path)

    measure_query = parse_goal_query(body.measure_query)
    if isinstance(measure_query, str):
        return None, None, None, f"measure_query: {measure_query}"
    if measure_query.scale is not None:
        return None, None, None, "scale belongs on reference_query (it's the multiplier on what you compare against)"

    has_amount = body.reference_amount is not None
    has_query = body.reference_query is not None
    if has_amount == has_query:
        return None, None, None, "exactly one of reference_amount, reference_query is required"

    reference_query = None
    if has_query:
        reference_query = parse_goal_query(body.reference_query)
        if isinstance(reference_query, str):
            return None, None, None, f"reference_query: {reference_query}"
        reference_query = goal_query_to_json(reference_query)

    if reference_query is not None:
        reference_query = _ensure_consumption_filter(reference_query)
    return _ensure_consumption_filter(goal_query_to_json(measure_query)), (body.reference_amount if not has_query else None), reference_query, None


def _find_existing_duplicates(goals: list[dict]) -> dict[int, int]:
    """{goal id: id of the goal it duplicates}, for goals that already
    duplicate another active one -- possible for data written before the
    duplicate check existed (a hand-made protein floor beside the macro
    target). Nothing is deleted automatically (the user's own goal may carry
    a number they chose); the list just flags the extra so it can be
    removed. The one kept is the preset if there is one, else the oldest."""
    groups: dict[str, list[dict]] = {}
    for g in goals:
        if g["is_active"]:
            groups.setdefault(signature_of_goal(g), []).append(g)
    result: dict[int, int] = {}
    for members in groups.values():
        if len(members) > 1:
            keeper = min(members, key=lambda g: (g["source"] not in PRESET_SOURCES, g["id"]))
            for g in members:
                if g["id"] != keeper["id"]:
                    result[g["id"]] = keeper["id"]
    return result


@router.get("")
async def list_goals(active: Optional[str] = None, include_system: Optional[str] = None, user_id: int = Depends(get_current_user)):
    rows = await goals_query.list_goals(user_id, active_only=(active == "true"), include_system=(include_system == "true"))
    defaults = _preset_defaults(await profile_query.get_profile(user_id))
    duplicate_of = _find_existing_duplicates(rows)
    return [{**_serialize_goal(g, defaults), "duplicate_of": duplicate_of.get(g["id"])} for g in rows]


@router.post("", status_code=201)
async def create_goal(body: GoalInput, user_id: int = Depends(get_current_user)):
    if not is_valid_comparator(body.comparator):
        raise HTTPException(status_code=400, detail="comparator must be one of: lte, gte, eq, within_tolerance_percent")
    if body.comparator == "within_tolerance_percent" and body.tolerance_percent is None:
        raise HTTPException(status_code=400, detail="tolerance_percent is required when comparator is within_tolerance_percent")
    if _is_shorthand(body) and body.comparator == "within_tolerance_percent":
        raise HTTPException(status_code=400, detail="comparator must be one of: lte, gte, eq for shorthand input")
    if not is_valid_severity(body.severity):
        raise HTTPException(status_code=400, detail="severity must be one of: warning, target")

    measure_query, reference_amount, reference_query, err = _parse_goal_input(body)
    if err:
        raise HTTPException(status_code=400, detail=err)
    if measure_query is None:
        raise HTTPException(status_code=400, detail="measure_query (or category/target_amount/period shorthand) is required")

    try:
        goal = await goals_query.create_goal(
            user_id=user_id,
            label=body.label,
            severity=body.severity,
            comparator=body.comparator,
            tolerance_percent=body.tolerance_percent if body.comparator == "within_tolerance_percent" else None,
            measure_query=measure_query,
            reference_amount=reference_amount,
            reference_query=reference_query,
            inflation_adjusted=body.inflation_adjusted,
            notify_on_crossing=body.notify_on_crossing,
        )
    except DuplicateGoalError as e:
        raise _duplicate_conflict(e)
    return await _serialize(goal, user_id)


@router.patch("/{goal_id}")
async def update_goal(goal_id: int, body: GoalUpdateInput, user_id: int = Depends(get_current_user)):
    if body.comparator is not None and not is_valid_comparator(body.comparator):
        raise HTTPException(status_code=400, detail="comparator must be one of: lte, gte, eq, within_tolerance_percent")
    if body.severity is not None and not is_valid_severity(body.severity):
        raise HTTPException(status_code=400, detail="severity must be one of: warning, target")
    if body.comparator == "within_tolerance_percent" and body.tolerance_percent is None:
        raise HTTPException(status_code=400, detail="tolerance_percent is required when comparator is within_tolerance_percent")

    current = await goals_query.get_owned_goal(goal_id, user_id)
    if not current:
        raise HTTPException(status_code=404, detail="Goal not found")

    has_query_edit = any(v is not None for v in (
        body.measure_query, body.reference_query, body.category, body.measure_field, body.vital, body.target_amount, body.period,
    ))

    if current["source"] in PRESET_SOURCES:
        sent = body.model_dump(exclude_unset=True)
        locked = [k for k in sent if k not in PRESET_EDITABLE_FIELDS]
        if locked or has_query_edit:
            raise HTTPException(
                status_code=400,
                detail="A preset goal's amount can be edited (or reset to its default), but not what it measures -- create your own goal for a different shape.",
            )

    updates: dict = {}
    if body.label is not None:
        updates["label"] = body.label
    if body.severity is not None:
        updates["severity"] = body.severity
    if body.is_active is not None:
        updates["is_active"] = body.is_active
    if body.comparator is not None:
        updates["comparator"] = body.comparator
    if body.tolerance_percent is not None:
        updates["tolerance_percent"] = body.tolerance_percent
    if body.inflation_adjusted is not None:
        updates["inflation_adjusted"] = body.inflation_adjusted
    if body.notify_on_crossing is not None:
        updates["notify_on_crossing"] = body.notify_on_crossing

    if has_query_edit or body.measure_query is not None:
        measure_query, reference_amount, reference_query, err = _parse_goal_input(body)
        if err:
            raise HTTPException(status_code=400, detail=err)
        if measure_query is not None:
            updates["measure_query"] = measure_query
        updates["reference_amount"] = reference_amount
        updates["reference_query"] = reference_query
    elif body.reference_scale is not None:
        if current["reference_query"] is None:
            raise HTTPException(status_code=400, detail="reference_scale only applies to a goal compared against a computed value")
        parsed = parse_goal_query({**current["reference_query"], "scale": body.reference_scale})
        if isinstance(parsed, str):
            raise HTTPException(status_code=400, detail=f"reference_scale: {parsed}")
        updates["reference_query"] = goal_query_to_json(parsed)
    elif body.reference_amount is not None:
        # Amount-only edit (the inline "change the number" path): leaves
        # the query alone. Only meaningful for a constant reference.
        if current["reference_query"] is not None:
            raise HTTPException(status_code=400, detail="This goal is compared against a computed baseline, not a fixed amount")
        updates["reference_amount"] = body.reference_amount
        if current["source"] == "dri_default":
            updates["source"] = "user_target"  # customized: a profile change must no longer overwrite it

    try:
        goal = await goals_query.update_goal(goal_id, user_id, updates)
    except DuplicateGoalError as e:
        raise _duplicate_conflict(e)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    return await _serialize(goal, user_id)


@router.delete("/{goal_id}", status_code=204)
async def delete_goal(goal_id: int, user_id: int = Depends(get_current_user)):
    current = await goals_query.get_owned_goal(goal_id, user_id)
    if current and current["source"] in PRESET_SOURCES:
        # A preset is removed by turning it off, not by deleting its row: seeding
        # (a profile change) would otherwise put it straight back. Reset
        # (POST /goals/:id/reset, POST /goals/presets/reset) brings it back.
        await targets_query.deactivate_goal(goal_id, user_id)
        return
    deleted = await goals_query.delete_goal(goal_id, user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Goal not found")


def _status_body(evaluated) -> dict:
    return {
        "measure_value": evaluated.measure_value,
        "reference_value": evaluated.reference_value,
        "comparator": evaluated.comparator,
        "tolerance_percent": evaluated.tolerance_percent,
        "percent": evaluated.percent,
        "on_track": evaluated.is_compliant,
        "severity": evaluated.severity,
        "has_data": evaluated.has_data,
    }


async def _read_goal_status(goal: dict) -> dict:
    """A pure, non-mutating read -- goals is pure definition (no
    last_status to touch), so this and the event-triggered path
    (goals_evaluation.evaluate_goal_transition) both call the exact same
    compute_goal_status, and can never disagree on the number.
    Celebratory/warning logging happens only from the event-triggered
    path, never from a status read."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        evaluated = await compute_goal_status(conn, goal)
    return _status_body(evaluated)


def _is_daily_nutrient_goal(goal: dict) -> bool:
    """A plain "today's total of one nutrient vs. a fixed amount" goal --
    every DRI preset is one -- whose status one grouped query can answer
    for all of them at once."""
    mq = goal["measure_query"]
    tw = mq.get("timeWindow", {})
    return (
        goal.get("source") in PRESET_SOURCES
        and goal["comparator"] in ("gte", "lte")
        and mq.get("measureField") is not None
        and mq.get("aggregation") == "sum"
        and tw.get("kind") == "current_period" and tw.get("period") == "daily"
        and goal["reference_query"] is None
    )


@router.get("/statuses")
async def get_goal_statuses(user_id: int = Depends(get_current_user)):
    """Live status for every active goal in one call -- the goals page
    shows ~60 presets at once, and one request per goal would be ~60
    round trips. The daily nutrient presets are answered from a single
    grouped query (today's totals for every nutrient); every other goal
    goes through the same compute_goal_status a single-goal read uses."""
    goals = await goals_query.list_goals(user_id, active_only=True, include_system=True)
    today = datetime.now(timezone.utc).date().isoformat()
    statuses = {}
    pool = await get_pool()
    async with pool.acquire() as conn:
        totals = None
        for goal in goals:
            if _is_daily_nutrient_goal(goal):
                if totals is None:
                    totals = await compute_nutrient_totals_for_date(conn, user_id, today)
                measure = round(totals.get(goal["measure_query"]["measureField"][len("nutrient:"):], 0.0), 2)
                reference = goal["reference_amount"]
                compliant = is_compliant(goal["comparator"], measure, reference, goal["tolerance_percent"])
                statuses[goal["id"]] = {
                    "measure_value": measure, "reference_value": round(reference, 2), "comparator": goal["comparator"],
                    "tolerance_percent": goal["tolerance_percent"], "percent": percent_of_reference(measure, reference),
                    "on_track": compliant, "severity": goal["severity"], "has_data": True,
                }
            else:
                statuses[goal["id"]] = _status_body(await compute_goal_status(conn, goal))
    return {"statuses": statuses}


@router.get("/measures")
async def get_measures():
    """Everything a goal can measure, grouped: calories + each nutrient
    (Macros / Vitamins / Minerals / Other -- nutrient_facts, via
    measureField "nutrient:<Name>"), and the vitals (weight, body fat).
    Each carries its standard unit. Static reference data, not
    user-scoped, so any client builds the same picker."""
    return {"measures": list_measures(), "groups": list(GROUP_ORDER)}


# Named starter configs. Basic ones are pre-filled shorthand bodies;
# `amount_hint` is a sensible starting number the person edits before
# creating. The list can grow without a schema change.
_BASIC_PRESETS = [
    {"name": "Daily calorie cap", "comparator": "lte", "period": "daily", "amount_hint": 2000},
    {"name": "Weekly calorie budget", "comparator": "lte", "period": "weekly", "amount_hint": 14000},
    {"name": "Daily protein floor", "comparator": "gte", "period": "daily", "measure_field": "nutrient:Protein", "amount_hint": 120},
    {"name": "Daily fiber floor", "comparator": "gte", "period": "daily", "measure_field": "nutrient:Fiber, total dietary", "amount_hint": 30},
    {"name": "Daily sodium ceiling", "comparator": "lte", "period": "daily", "measure_field": "nutrient:Sodium, Na", "amount_hint": 2300},
    {"name": "Daily added-sugar watch", "comparator": "lte", "period": "daily", "measure_field": "nutrient:Sugars, total", "amount_hint": 50},
    {"name": "Weekly protein total", "comparator": "gte", "period": "weekly", "measure_field": "nutrient:Protein", "amount_hint": 840},
    {"name": "Goal weight", "comparator": "lte", "vital": "weight", "amount_hint": 180},
    {"name": "Body-fat ceiling", "comparator": "lte", "vital": "body_fat", "amount_hint": 20},
]

def _ratio_preset(name: str, comparator: str, measure_field: str, ref_query: dict, scale: float) -> dict:
    """A goal compared against a computation on ANOTHER measure: the day's
    total of one nutrient vs. `scale` x the day's total (or latest reading)
    of another -- i.e. a ratio between them. The scale also converts the
    reference measure's unit into this one's."""
    return {
        "name": name,
        "comparator": comparator,
        "measure_query": {"aggregation": "sum", "measureField": measure_field, "filters": [], "timeWindow": {"kind": "current_period", "period": "daily"}},
        "reference_query": {**ref_query, "scale": scale},
    }


_DAILY_CALORIES = {"aggregation": "sum", "filters": [], "timeWindow": {"kind": "current_period", "period": "daily"}}

_ADVANCED_PRESETS = [
    # Ratios between measures. Protein/carbs are 4 kcal/g and fat 9 kcal/g,
    # so "25% of calories" as grams is 0.25/4 = 0.0625 g per kcal.
    _ratio_preset("Protein ≥ 25% of calories", "gte", "nutrient:Protein", _DAILY_CALORIES, 0.0625),
    _ratio_preset("Fat ≤ 35% of calories", "lte", "nutrient:Total lipid (fat)", _DAILY_CALORIES, round(0.35 / 9, 4)),
    _ratio_preset("Sodium ≤ 1 mg per calorie", "lte", "nutrient:Sodium, Na", _DAILY_CALORIES, 1),
    _ratio_preset("Fiber ≥ 14 g per 1,000 calories", "gte", "nutrient:Fiber, total dietary", _DAILY_CALORIES, 0.014),
    _ratio_preset("Protein ≥ 0.8 g per lb of body weight", "gte", "nutrient:Protein", vital_measure_query("weight"), 0.8),
    {
        "name": "Rolling 7-day average, ±15%",
        "comparator": "within_tolerance_percent",
        "tolerance_percent": 15,
        "measure_query": {"aggregation": "sum", "filters": [], "timeWindow": {"kind": "current_period", "period": "daily"}},
        "reference_query": {"aggregation": "mean", "filters": [], "timeWindow": {"kind": "trailing", "period": "daily", "count": 7}},
    },
    {
        "name": "Week-over-week trend, ±10%",
        "comparator": "within_tolerance_percent",
        "tolerance_percent": 10,
        "measure_query": {"aggregation": "sum", "filters": [], "timeWindow": {"kind": "current_period", "period": "weekly"}},
        "reference_query": {"aggregation": "mean", "filters": [], "timeWindow": {"kind": "trailing", "period": "weekly", "count": 1}},
    },
    {
        "name": "Month-over-month calories, ±10%",
        "comparator": "within_tolerance_percent",
        "tolerance_percent": 10,
        "measure_query": {"aggregation": "sum", "filters": [], "timeWindow": {"kind": "current_period", "period": "monthly"}},
        "reference_query": {"aggregation": "mean", "filters": [], "timeWindow": {"kind": "trailing", "period": "monthly", "count": 1}},
    },
    {
        "name": "This month vs. trailing 3-month average, ±10%",
        "comparator": "within_tolerance_percent",
        "tolerance_percent": 10,
        "measure_query": {"aggregation": "sum", "filters": [], "timeWindow": {"kind": "current_period", "period": "monthly"}},
        "reference_query": {"aggregation": "mean", "filters": [], "timeWindow": {"kind": "trailing", "period": "monthly", "count": 3}},
    },
    {
        "name": "Outlier watch (95th percentile)",
        "comparator": "lte",
        "measure_query": {"aggregation": "percentile", "percentile": 95, "filters": [], "timeWindow": {"kind": "current_period", "period": "daily"}},
        "reference_query": {"aggregation": "percentile", "percentile": 95, "filters": [], "timeWindow": {"kind": "all_time"}},
    },
]


def _preset_signature(preset: dict) -> str:
    if "measure_query" in preset:
        return goal_signature(
            _ensure_consumption_filter(preset["measure_query"]), _ensure_consumption_filter(preset["reference_query"]),
            preset["comparator"], "target",
        )
    query, _ = _expand_shorthand(None, preset.get("measure_field"), preset.get("vital"), preset["amount_hint"], preset.get("period"))
    return goal_signature(query, None, preset["comparator"], "target")


@router.get("/presets")
async def get_presets(user_id: int = Depends(get_current_user)):
    """Named starter configs, both basic (shorthand) and advanced (full
    measure_query/reference_query) tiers -- pre-filled request bodies the
    person edits before creating (amount, comparator, period all stay
    changeable), no server-side concept beyond this list. Each carries
    `existing_goal_id` when the user already has a goal asserting the same
    thing, so the client can point at it ("edit it") instead of offering
    to add a second one -- the create call would 409 anyway. A basic
    preset may carry `measure_field` (a nutrient instead of calories) or
    `vital`; the advanced tier is where long-term goals live: weekly and
    monthly periods, trailing baselines, month-over-month comparisons."""
    existing = {}
    for g in await goals_query.list_active_goals_for_signatures(user_id):
        existing.setdefault(signature_of_goal(g), g["id"])

    def decorate(preset: dict) -> dict:
        info = describe_measure(_expand_shorthand(None, preset.get("measure_field"), preset.get("vital"), 0, preset.get("period"))[0]) if "measure_query" not in preset else None
        return {
            **preset,
            **({"group": info["group"], "unit": info["unit"]} if info else {}),
            "existing_goal_id": existing.get(_preset_signature(preset)),
        }

    return {"basic": [decorate(p) for p in _BASIC_PRESETS], "advanced": [decorate(p) for p in _ADVANCED_PRESETS]}


async def _require_profile_defaults(user_id: int) -> tuple[dict, dict]:
    profile = await profile_query.get_profile(user_id)
    if profile is None:
        raise HTTPException(status_code=400, detail="Set your profile (age + sex) first -- preset defaults are derived from it")
    return profile, _preset_defaults(profile)


# Declared before /{goal_id}/... so "presets" is never parsed as a goal id.
@router.post("/presets/reset")
async def reset_all_presets(user_id: int = Depends(get_current_user)):
    """Restores every DRI preset to its default: customized amounts go
    back, deleted ones return, and any default missing for this profile is
    re-added."""
    profile, defaults = await _require_profile_defaults(user_id)
    restored = 0
    all_goals = await goals_query.list_goals(user_id, active_only=False, include_system=True)
    # A deleted preset is only brought back if the user hasn't since made their
    # own goal covering the same thing (that would be two of the same goal).
    covered = {signature_of_goal(g) for g in all_goals if g["is_active"]}
    for goal in all_goals:
        if goal["source"] not in ("dri_default", "user_target"):
            continue
        if not goal["is_active"] and signature_of_goal(goal) in covered:
            continue
        default = _default_amount_for(goal, defaults)
        if default is not None and (goal["reference_amount"] != default or goal["source"] != "dri_default" or not goal["is_active"]):
            await targets_query.restore_preset_goal(goal["id"], user_id, default, "dri_default")
            restored += 1
    await seed_dri_targets(user_id, profile["sex"], profile["age"])
    return {"reset": restored}


@router.post("/{goal_id}/reset")
async def reset_goal_to_default(goal_id: int, user_id: int = Depends(get_current_user)):
    """Puts one preset back to its default amount. A DRI preset also
    returns to tracking the default (a later profile change refreshes it
    again); a macro target keeps being a macro target."""
    goal = await goals_query.get_owned_goal(goal_id, user_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    _profile, defaults = await _require_profile_defaults(user_id)
    default = _default_amount_for(goal, defaults)
    if default is None:
        raise HTTPException(status_code=400, detail="This goal has no default to reset to")
    if not goal["is_active"]:
        clash = next((g for g in await goals_query.list_active_goals_for_signatures(user_id) if signature_of_goal(g) == signature_of_goal(goal)), None)
        if clash:
            raise _duplicate_conflict(DuplicateGoalError(clash))
    await targets_query.restore_preset_goal(goal_id, user_id, default, "macro_target" if goal["source"] == "macro_target" else "dri_default")
    return await _serialize(await goals_query.get_owned_goal(goal_id, user_id), user_id)


@router.get("/{goal_id}/status")
async def get_goal_status(goal_id: int, user_id: int = Depends(get_current_user)):
    goal = await goals_query.get_owned_goal(goal_id, user_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    return await _read_goal_status(goal)
