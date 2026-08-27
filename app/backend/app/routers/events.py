"""
Universal Event Contract adapter — translates this tracker's real domain
tables (food_log, exercise_log) into TrackStack's Core Event Shape
(see workspace-notes/EVENT_CONTRACT_SPEC.md) and back.

This is deliberately an ADDITIONAL layer, not a replacement for the
domain-specific endpoints (POST /food/log, POST /exercise, etc.), which
stay exactly as they are and remain the primary way this app's own
frontend talks to its own backend. This adapter exists so a cross-app
consumer (trackstack-notifications, a future unified dashboard, a
to-do-list event matcher) can query ANY tracker through one uniform
shape/URL pattern (GET /events, POST /events/log, GET /aggregations/...)
without knowing food_log's or bankTransactions' internal column names —
see EVENT_CONTRACT_SPEC.md's "Resolved Decisions #4" for why this was
built as real routes instead of a shape-only convention.

Two event_types are exposed today, proving the design generalizes across
genuinely different underlying tables, not just one:
  - "food_entry"       -> food_log (+ food_log_nutrients)
  - "exercise_activity" -> exercise_log

Known, explicitly tracked gap (see workspace-notes/ACTION_ITEMS.md): the
Core Event Shape's `category` field is NOT populated here. Neither
food_log nor exercise_log stores a category today — this was flagged
when the Event Contract was drafted (Cronometer's CSV `Category` column
is currently discarded during sync, and USDA/CNF category data has never
been wired in). Rather than fabricate a value, every event from this
adapter reports `category: null`. GET /aggregations/by_category still
works, but degenerates to a single "uncategorized" bucket until that
separate, already-tracked migration lands. `hidden` and `status` are
likewise always their defaults (false / null) — neither concept exists
in this tracker's schema yet.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..routers.auth import get_current_user
from ..db import get_pool
from ..nutrient_groups import order_nutrients
from ..food_entry_contract import (
    FoodLogEntryContract, ExerciseLogContract, log_food_entry, log_exercise_entry,
)

# Two router objects, mounted at two different prefixes in app/__init__.py
# (/events and /aggregations) — the contract defines GET /aggregations/{type}
# as its own top-level path, not nested under /events, so a single router
# object at one prefix can't express both.
router = APIRouter()
aggregations_router = APIRouter()

VALID_EVENT_TYPES = {"food_entry", "exercise_activity"}


class EventLogRequest(BaseModel):
    """Core Event Shape, as a request body. `metadata`'s expected keys
    are event_type-specific (see _dispatch_log below) — this is a
    deliberately thin, generic envelope, not a strict per-type schema,
    matching how the contract itself defines `metadata` as tracker/
    event_type-defined rather than fixed."""
    event_type: str
    occurred_at: str  # maps to food_log.date / exercise_log.date
    amount: float = 0
    source: Optional[str] = None
    source_id: Optional[str] = None
    category: Optional[str] = None  # accepted, NOT persisted — see module docstring
    hidden: bool = False            # accepted, NOT persisted — no such column yet
    status: Optional[str] = None    # accepted, NOT persisted — no such column yet
    metadata: dict = {}


async def _dispatch_log(user_id: int, req: EventLogRequest) -> dict:
    if req.event_type == "food_entry":
        food_name = req.metadata.get("food_name")
        if not food_name:
            raise HTTPException(status_code=400, detail="metadata.food_name is required for event_type=food_entry")
        entry = FoodLogEntryContract(
            date=req.occurred_at,
            meal=req.metadata.get("meal", "Snack"),
            food_name=food_name,
            source=req.source,
            source_id=req.source_id,
            serving_size=req.metadata.get("serving_size", 1.0),
            serving_unit=req.metadata.get("serving_unit", "serving"),
            calories=req.amount,
            nutrients=req.metadata.get("nutrients", {}),
        )
        food_log_id = await log_food_entry(user_id, entry)
        return {"id": food_log_id}

    if req.event_type == "exercise_activity":
        activity_name = req.metadata.get("activity_name")
        if not activity_name:
            raise HTTPException(status_code=400, detail="metadata.activity_name is required for event_type=exercise_activity")
        entry_id, _ = await log_exercise_entry(user_id, ExerciseLogContract(
            date=req.occurred_at,
            activity_name=activity_name,
            duration_minutes=req.metadata.get("duration_minutes"),
            calories_burned=req.amount,
            source=req.source or "manual",
            source_id=req.source_id,
            notes=req.metadata.get("notes"),
        ))
        return {"id": entry_id}

    raise HTTPException(
        status_code=400,
        detail=f"unknown event_type {req.event_type!r} — must be one of {sorted(VALID_EVENT_TYPES)}",
    )


@router.post("/log")
async def log_event(req: EventLogRequest, user_id: int = Depends(get_current_user)):
    """Universal event ingestion. Dispatches to the same
    food_entry_contract.log_food_entry()/log_exercise_entry() functions
    the domain-specific endpoints (POST /food/log, POST /exercise) use —
    this adapter never writes SQL of its own, so a future change to
    storage/validation logic in those shared functions automatically
    applies here too, the same decoupling rationale food_entry_contract.py
    already documents for every other caller."""
    result = await _dispatch_log(user_id, req)
    return {"status": "logged", **result}


def _food_row_to_event(r, nutrients: dict) -> dict:
    return {
        "id": r["id"],
        "user_id": r["user_id"],
        "event_type": "food_entry",
        "category": None,  # see module docstring — known, tracked gap
        "occurred_at": r["date"],
        "created_at": r["created_at"].isoformat(),
        "amount": r["calories"],
        "source": r["source"],
        "source_id": r["source_id"],
        "hidden": False,
        "status": None,
        "metadata": {
            "food_name": r["food_name"],
            "meal": r["meal"],
            "serving_size": r["serving_size"],
            "serving_unit": r["serving_unit"],
            "nutrients": nutrients,
        },
    }


def _exercise_row_to_event(r) -> dict:
    return {
        "id": r["id"],
        "user_id": r["user_id"],
        "event_type": "exercise_activity",
        "category": None,  # see module docstring — known, tracked gap
        "occurred_at": r["date"],
        "created_at": r["created_at"].isoformat(),
        "amount": r["calories_burned"],
        "source": r["source"],
        "source_id": r["source_id"],
        "hidden": False,
        "status": None,
        "metadata": {
            "activity_name": r["activity_name"],
            "duration_minutes": r["duration_minutes"],
            "notes": r["notes"],
        },
    }


@router.get("")
async def get_events(
    start: str = Query(..., description="Inclusive start date, YYYY-MM-DD"),
    end: str = Query(..., description="Inclusive end date, YYYY-MM-DD"),
    event_type: Optional[str] = Query(None, description="Filter to one event_type; omit for all"),
    source: Optional[str] = Query(None, description="Filter to one source; omit for all"),
    user_id: int = Depends(get_current_user),
):
    """Universal event query across every event_type this tracker
    exposes. No pagination yet (next_page_token in the contract spec is
    unimplemented) — matches every other list endpoint in this app today
    (GET /food/log, GET /exercise), none of which paginate either; added
    if/when a real consumer needs it rather than speculatively."""
    events = await _query_events(user_id, start, end, event_type, source)
    return {"events": events, "total": len(events)}


async def _query_events(
    user_id: int, start: str, end: str,
    event_type: Optional[str] = None, source: Optional[str] = None,
) -> list[dict]:
    """Shared query logic behind GET /events and GET /aggregations/{type}
    — isolated here (rather than one route calling the other directly)
    so aggregation can reuse the exact same event set without going
    through FastAPI's dependency-injection machinery a second time,
    matching this codebase's existing convention of a private data
    helper backing one or more route handlers (e.g. routers/recipes.py's
    _get_recipe_with_items)."""
    if event_type is not None and event_type not in VALID_EVENT_TYPES:
        raise HTTPException(status_code=400, detail=f"unknown event_type {event_type!r} — must be one of {sorted(VALID_EVENT_TYPES)}")

    events: list[dict] = []
    pool = await get_pool()

    if event_type is None or event_type == "food_entry":
        async with pool.acquire() as conn:
            food_rows = await conn.fetch(
                "SELECT * FROM food_log WHERE user_id = $1 AND date >= $2 AND date <= $3 ORDER BY date",
                user_id, start, end,
            )
            entry_ids = [r["id"] for r in food_rows]
            nutrient_rows = []
            if entry_ids:
                nutrient_rows = await conn.fetch(
                    "SELECT food_log_id, nutrient_name, value, unit FROM food_log_nutrients WHERE food_log_id = ANY($1::int[])",
                    entry_ids,
                )
        nutrients_by_entry: dict[int, dict] = {}
        for nr in nutrient_rows:
            nutrients_by_entry.setdefault(nr["food_log_id"], {})[nr["nutrient_name"]] = {
                "value": nr["value"], "unit": nr["unit"],
            }
        events.extend(_food_row_to_event(r, order_nutrients(nutrients_by_entry.get(r["id"], {}))) for r in food_rows)

    if event_type is None or event_type == "exercise_activity":
        async with pool.acquire() as conn:
            exercise_rows = await conn.fetch(
                "SELECT * FROM exercise_log WHERE user_id = $1 AND date >= $2 AND date <= $3 ORDER BY date",
                user_id, start, end,
            )
        events.extend(_exercise_row_to_event(r) for r in exercise_rows)

    if source is not None:
        events = [e for e in events if e["source"] == source]

    events.sort(key=lambda e: (e["occurred_at"], e["id"]))
    return events


@aggregations_router.get("/{agg_type}")
async def get_aggregations(
    agg_type: str,
    start: str = Query(..., description="Inclusive start date, YYYY-MM-DD"),
    end: str = Query(..., description="Inclusive end date, YYYY-MM-DD"),
    user_id: int = Depends(get_current_user),
):
    """Sums `amount` (calories / calories_burned — both kcal, so a single
    unit across every group is honest here, not a coincidence masking a
    units bug) grouped by the requested dimension.

    agg_type="by_category" is implemented but degenerates to one
    "uncategorized" bucket today — see module docstring. Still returned
    (not 501/omitted) since even a single honest bucket is useful to a
    caller and it keeps the endpoint's shape stable for when category
    data lands, rather than changing shape out from under consumers."""
    if agg_type not in ("by_category", "by_source", "by_event_type"):
        raise HTTPException(status_code=400, detail="agg_type must be one of: by_category, by_source, by_event_type")

    events = await _query_events(user_id, start, end)

    key_fn = {
        "by_category": lambda e: e["category"] or "uncategorized",
        "by_source": lambda e: e["source"] or "unknown",
        "by_event_type": lambda e: e["event_type"],
    }[agg_type]

    totals: dict[str, float] = {}
    for e in events:
        key = key_fn(e)
        totals[key] = totals.get(key, 0.0) + (e["amount"] or 0)

    group_key = agg_type.replace("by_", "")
    return {"data": [{group_key: k, "total_amount": round(v, 2), "unit": "kcal"} for k, v in sorted(totals.items())]}
