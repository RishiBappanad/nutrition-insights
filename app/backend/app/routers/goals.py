"""
Goals API -- Python/FastAPI port of finance-tracker's routes/goals.ts.
See workspace-notes/RECURRING_AND_GOALS_SPEC.md for the full cross-
tracker design; goal_query.py/goals_evaluation.py are this tracker's own
copies of the shared interpreter/evaluator finance-tracker's lib/db
already implements.

`inflation_adjusted` is accepted and stored for schema parity across
trackers, but goals_evaluation.py's compute_goal_status never actually
adjusts anything with it here (see that module's own note) -- CPI
inflation is a money concept finance-tracker's Goals uses, not a
nutrition one.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, Union

from .auth import get_current_user
from ..db.goals import query as goals_query
from ..db import get_pool
from ..goal_query import (
    parse_goal_query, is_valid_period, is_valid_measure_field, GOAL_QUERY_PERIODS, goal_query_to_json,
)
from ..goals_evaluation import (
    is_valid_comparator, is_valid_severity, compute_goal_status,
)
from ..nutrition_targets import nutrient_unit, DRI_TABLE

router = APIRouter()


class GoalInput(BaseModel):
    # Shorthand fields (basic goal). `category` (a food category) and
    # `measure_field` ("nutrient:<Name>", omitted = calories) are both
    # optional narrowing choices; target_amount + period are what make it
    # shorthand.
    category: Optional[str] = None
    measure_field: Optional[str] = None
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
    target_amount: Optional[float] = None
    period: Optional[str] = None
    measure_query: Optional[dict] = None
    reference_amount: Optional[float] = None
    reference_query: Optional[dict] = None


def _is_shorthand(body: GoalInput) -> bool:
    return body.target_amount is not None and body.period is not None and body.measure_query is None


_CONSUMPTION_FILTER = {"field": "owner_type", "operator": "eq", "value": "food_log"}


def _ensure_consumption_filter(query: dict) -> dict:
    """A nutrition goal measures what was EATEN. domain_events here also
    carries pantry stock, recipes, meals, custom foods, and exercise --
    all with `amount`/nutrient_facts of their own -- so an unfiltered
    query would count a pantry restock, or calories burned, toward a
    calorie goal. Added server-side (unless the caller already filters on
    owner_type themselves) so no client has to remember it."""
    if any(f.get("field") == "owner_type" for f in query["filters"]):
        return query
    return {**query, "filters": [*query["filters"], dict(_CONSUMPTION_FILTER)]}


def _expand_shorthand(category: Optional[str], measure_field: Optional[str], target_amount: float, period: str) -> tuple[dict, float]:
    """Expands the flat shorthand into the same stored shape the full
    form uses -- see RECURRING_AND_GOALS_SPEC.md's "Basic goals stay
    simple" section. A basic goal never requires hand-building the full
    query JSON."""
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


def _goal_unit(measure_query: dict) -> str:
    field = measure_query.get("measureField")
    return nutrient_unit(field[len("nutrient:"):]) if field else "cal"


def _serialize_goal(g: dict) -> dict:
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
        "inflation_adjusted": g["inflation_adjusted"],
        "notify_on_crossing": g["notify_on_crossing"],
        "source": g.get("source"),
        "unit": _goal_unit(g["measure_query"]),
        "created_at": g["created_at"].isoformat(),
        "updated_at": g["updated_at"].isoformat(),
    }


def _parse_goal_input(body: Union[GoalInput, GoalUpdateInput]) -> tuple[Optional[dict], Optional[float], Optional[dict], Optional[str]]:
    """Returns (measure_query, reference_amount, reference_query, error).
    Shared by create and update: accepts either shorthand
    (category/target_amount/period) or full-form (measure_query/
    reference_amount/reference_query) input."""
    if body.category is not None or body.measure_field is not None or body.target_amount is not None or body.period is not None:
        if body.measure_query is not None:
            return None, None, None, "cannot mix shorthand (category/measure_field/target_amount/period) with measure_query"
        if body.target_amount is None or body.period is None:
            return None, None, None, "shorthand requires target_amount and period"
        if not is_valid_period(body.period):
            return None, None, None, f"period must be one of: {', '.join(GOAL_QUERY_PERIODS)}"
        if body.measure_field is not None and not is_valid_measure_field(body.measure_field):
            return None, None, None, "measure_field must be \"nutrient:<Name>\" or omitted"
        measure_query, reference_amount = _expand_shorthand(body.category, body.measure_field, body.target_amount, body.period)
        return measure_query, reference_amount, None, None

    if body.measure_query is None:
        return None, None, None, None  # no query edit requested (update-only path)

    measure_query = parse_goal_query(body.measure_query)
    if isinstance(measure_query, str):
        return None, None, None, f"measure_query: {measure_query}"

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


@router.get("")
async def list_goals(active: Optional[str] = None, include_system: Optional[str] = None, user_id: int = Depends(get_current_user)):
    rows = await goals_query.list_goals(user_id, active_only=(active == "true"), include_system=(include_system == "true"))
    return [_serialize_goal(g) for g in rows]


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
    return _serialize_goal(goal)


@router.patch("/{goal_id}")
async def update_goal(goal_id: int, body: GoalUpdateInput, user_id: int = Depends(get_current_user)):
    if body.comparator is not None and not is_valid_comparator(body.comparator):
        raise HTTPException(status_code=400, detail="comparator must be one of: lte, gte, eq, within_tolerance_percent")
    if body.severity is not None and not is_valid_severity(body.severity):
        raise HTTPException(status_code=400, detail="severity must be one of: warning, target")
    if body.comparator == "within_tolerance_percent" and body.tolerance_percent is None:
        raise HTTPException(status_code=400, detail="tolerance_percent is required when comparator is within_tolerance_percent")

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

    has_query_edit = any([body.measure_query, body.reference_amount is not None, body.reference_query, body.category, body.measure_field, body.target_amount is not None, body.period])
    if has_query_edit:
        measure_query, reference_amount, reference_query, err = _parse_goal_input(body)
        if err:
            raise HTTPException(status_code=400, detail=err)
        if measure_query is not None:
            updates["measure_query"] = measure_query
        updates["reference_amount"] = reference_amount
        updates["reference_query"] = reference_query

    goal = await goals_query.update_goal(goal_id, user_id, updates)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    return _serialize_goal(goal)


@router.delete("/{goal_id}", status_code=204)
async def delete_goal(goal_id: int, user_id: int = Depends(get_current_user)):
    deleted = await goals_query.delete_goal(goal_id, user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Goal not found")


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
    return {
        "measure_value": evaluated.measure_value,
        "reference_value": evaluated.reference_value,
        "comparator": evaluated.comparator,
        "tolerance_percent": evaluated.tolerance_percent,
        "percent": evaluated.percent,
        "on_track": evaluated.is_compliant,
        "severity": evaluated.severity,
    }


@router.get("/measures")
async def get_measures():
    """Everything a goal can measure: calories (domain_events.amount), or
    any nutrient (nutrient_facts, via measureField "nutrient:<Name>") --
    the macros plus every DRI-tracked micronutrient. Static reference
    data, not user-scoped, so any client builds the same picker."""
    measures = [
        {"field": None, "label": "Calories", "unit": "cal"},
        {"field": "nutrient:Protein", "label": "Protein", "unit": "g"},
        {"field": "nutrient:Carbohydrate, by difference", "label": "Carbohydrates", "unit": "g"},
        {"field": "nutrient:Total lipid (fat)", "label": "Fat", "unit": "g"},
    ]
    seen = {m["field"] for m in measures}
    for name, info in DRI_TABLE.items():
        field = f"nutrient:{name}"
        if field not in seen:
            measures.append({"field": field, "label": name, "unit": info["unit"]})
    return {"measures": measures}


@router.get("/presets")
async def get_presets():
    """Named starter configs, both basic (shorthand) and advanced (full
    measure_query/reference_query) tiers -- pre-filled request bodies
    only, no new server-side concept, so this list can grow without a
    schema change. A basic preset may carry `measure_field` (a nutrient
    instead of calories) and `amount_hint` (a sensible starting number);
    the user still picks/edits the amount and, optionally, a food
    category. The advanced tier is where long-term goals live: weekly and
    monthly periods, trailing baselines, month-over-month comparisons."""
    return {
        "basic": [
            {"name": "Daily calorie cap", "comparator": "lte", "period": "daily", "amount_hint": 2000},
            {"name": "Weekly calorie budget", "comparator": "lte", "period": "weekly", "amount_hint": 14000},
            {"name": "Daily protein floor", "comparator": "gte", "period": "daily", "measure_field": "nutrient:Protein", "amount_hint": 120},
            {"name": "Daily fiber floor", "comparator": "gte", "period": "daily", "measure_field": "nutrient:Fiber, total dietary", "amount_hint": 30},
            {"name": "Daily sodium ceiling", "comparator": "lte", "period": "daily", "measure_field": "nutrient:Sodium, Na", "amount_hint": 2300},
            {"name": "Weekly protein total", "comparator": "gte", "period": "weekly", "measure_field": "nutrient:Protein", "amount_hint": 840},
        ],
        "advanced": [
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
        ],
    }


@router.get("/{goal_id}/status")
async def get_goal_status(goal_id: int, user_id: int = Depends(get_current_user)):
    goal = await goals_query.get_owned_goal(goal_id, user_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    return await _read_goal_status(goal)
