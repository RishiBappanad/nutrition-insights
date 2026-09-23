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
    parse_goal_query, is_valid_period, GOAL_QUERY_PERIODS, goal_query_to_json,
)
from ..goals_evaluation import (
    is_valid_comparator, is_valid_severity, compute_goal_status,
)

router = APIRouter()


class GoalInput(BaseModel):
    # Shorthand fields (basic goal)
    category: Optional[str] = None
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
    target_amount: Optional[float] = None
    period: Optional[str] = None
    measure_query: Optional[dict] = None
    reference_amount: Optional[float] = None
    reference_query: Optional[dict] = None


def _is_shorthand(body: GoalInput) -> bool:
    return body.category is not None and body.target_amount is not None and body.period is not None and body.measure_query is None


def _expand_shorthand(category: str, target_amount: float, period: str) -> tuple[dict, float]:
    """Expands the flat shorthand into the same stored shape the full
    form uses -- see RECURRING_AND_GOALS_SPEC.md's "Basic goals stay
    simple" section. A basic goal never requires hand-building the full
    query JSON."""
    measure_query = {
        "aggregation": "sum",
        "filters": [{"field": "category", "operator": "eq", "value": category}],
        "timeWindow": {"kind": "current_period", "period": period},
    }
    return measure_query, target_amount


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
        "created_at": g["created_at"].isoformat(),
        "updated_at": g["updated_at"].isoformat(),
    }


def _parse_goal_input(body: Union[GoalInput, GoalUpdateInput]) -> tuple[Optional[dict], Optional[float], Optional[dict], Optional[str]]:
    """Returns (measure_query, reference_amount, reference_query, error).
    Shared by create and update: accepts either shorthand
    (category/target_amount/period) or full-form (measure_query/
    reference_amount/reference_query) input."""
    if body.category is not None or body.target_amount is not None or body.period is not None:
        if body.measure_query is not None:
            return None, None, None, "cannot mix shorthand (category/target_amount/period) with measure_query"
        if body.category is None or body.target_amount is None or body.period is None:
            return None, None, None, "shorthand requires category, target_amount, and period together"
        if not is_valid_period(body.period):
            return None, None, None, f"period must be one of: {', '.join(GOAL_QUERY_PERIODS)}"
        measure_query, reference_amount = _expand_shorthand(body.category, body.target_amount, body.period)
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

    return goal_query_to_json(measure_query), (body.reference_amount if not has_query else None), reference_query, None


@router.get("")
async def list_goals(active: Optional[str] = None, user_id: int = Depends(get_current_user)):
    rows = await goals_query.list_goals(user_id, active_only=(active == "true"))
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

    has_query_edit = any([body.measure_query, body.reference_amount is not None, body.reference_query, body.category, body.target_amount is not None, body.period])
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


@router.get("/presets")
async def get_presets():
    """Named starter configs, both basic (shorthand) and advanced (full
    measure_query/reference_query) tiers -- pre-filled request bodies
    only, no new server-side concept, so this list can grow without a
    schema change. Mirrors finance-tracker's own presets, substituting
    calories (this tracker's one universal per-entry numeric amount, see
    goal_query.py's module doc) wherever finance's used dollars."""
    return {
        "basic": [
            {"name": "Daily calorie cap", "comparator": "lte", "period": "daily"},
            {"name": "Weekly average cap", "comparator": "lte", "period": "weekly"},
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
                "name": "Outlier watch (95th percentile this year)",
                "comparator": "lte",
                "measure_query": {"aggregation": "percentile", "percentile": 95, "filters": [], "timeWindow": {"kind": "current_period", "period": "daily"}},
                "reference_query": {"aggregation": "percentile", "percentile": 95, "filters": [], "timeWindow": {"kind": "all_time"}},
            },
            {
                "name": "Week-over-week trend, ±10%",
                "comparator": "within_tolerance_percent",
                "tolerance_percent": 10,
                "measure_query": {"aggregation": "sum", "filters": [], "timeWindow": {"kind": "current_period", "period": "weekly"}},
                "reference_query": {"aggregation": "mean", "filters": [], "timeWindow": {"kind": "trailing", "period": "weekly", "count": 1}},
            },
        ],
    }


@router.get("/{goal_id}/status")
async def get_goal_status(goal_id: int, user_id: int = Depends(get_current_user)):
    goal = await goals_query.get_owned_goal(goal_id, user_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    return await _read_goal_status(goal)
