"""
Universal Event Contract adapter — translates this tracker's domain
mutations into TrackStack's Core Event Shape (see
workspace-notes/EVENT_CONTRACT_SPEC.md) and back.

This is deliberately an ADDITIONAL layer, not a replacement for the
domain-specific endpoints (POST /food/log, POST /exercise, POST
/pantry, etc.), which stay exactly as they are and remain the primary
way this app's own frontend talks to its own backend.

GET /events and GET /aggregations read from `domain_events` (see
db/__init__.py's table comment and app/domain_events.py) rather than
deriving events live from food_log/exercise_log's CURRENT rows --
deriving live can only ever answer "this exists right now," never
"this was updated" or "this was deleted after the fact." A pantry item
being consumed down to zero and removed is real, contract-worthy
information that would otherwise just vanish from the record.
event_type follows the "<entity>_<action>" convention
todo-tracker's todo_events already established (e.g.
"food_log_created", "pantry_item_deleted"), generalized here across
every entity that logs domain_events -- currently food_log,
exercise_log, and pantry_item; recipes/meals/custom_foods are not yet
instrumented (see ACTION_ITEMS.md for that follow-up).
"""
import json
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..routers.auth import get_current_user
from ..db import get_pool
from ..db.sql_builder import select_clause, where_clause, order_by_clause
from ..food_category import FoodCategory
from ..food_entry_contract import (
    FoodLogEntryContract, ExerciseLogContract, log_food_entry, log_exercise_entry,
    log_food_entries_bulk, log_exercise_entries_bulk, find_existing_by_source,
)

# Two router objects, mounted at two different prefixes in app/__init__.py
# (/events and /aggregations) — the contract defines GET /aggregations/{type}
# as its own top-level path, not nested under /events, so a single router
# object at one prefix can't express both.
router = APIRouter()
aggregations_router = APIRouter()

# Only creatable event_types via the generic POST /events/log -- this
# endpoint's whole reason for existing (matching finance/todo's own
# POST /events/log) is "log a new occurrence," never an update/delete,
# so only the "_created" variants are ever valid input here even though
# GET /events can return "_updated"/"_deleted" rows too (written by the
# domain-specific routes' own log_domain_event calls, not through here).
CREATABLE_EVENT_TYPES = {"food_log_created", "exercise_log_created"}


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
    category: Optional[FoodCategory] = None
    hidden: bool = False            # accepted, NOT persisted — no such column yet
    status: Optional[str] = None    # accepted, NOT persisted — no such column yet
    metadata: dict = {}


class Event(BaseModel):
    """Core Event Shape, as returned by GET /events -- see
    workspace-notes/EVENT_CONTRACT_SPEC.md."""
    id: int
    user_id: int
    event_type: str
    category: Optional[FoodCategory] = None
    occurred_at: str
    created_at: str
    amount: float
    source: Optional[str] = None
    source_id: Optional[str] = None
    hidden: bool = False
    status: Optional[str] = None
    metadata: dict = {}
    label: Optional[str] = None


class LogEventResponse(BaseModel):
    status: str
    id: int


class GetEventsResponse(BaseModel):
    events: list[Event]
    total: int


class AggregationsResponse(BaseModel):
    """Each row in `data` is a small dict with a dynamic group-dimension
    key (literally "category", "source", or "event_type" -- whichever
    `agg_type` was requested, see get_aggregations below) plus the fixed
    `total_amount`/`unit` fields. That dynamic key is why this isn't a
    list of a more strictly-typed row model: the field NAME itself
    varies by request, not just its value, which Pydantic can't express
    as a named field."""
    data: list[dict[str, Union[str, float]]]


def _to_food_entry(req: EventLogRequest) -> FoodLogEntryContract:
    """Pure parse, no I/O -- shared by the single and batch routes below.
    Raises ValueError (not HTTPException) so the batch route can prefix
    its own events[i] index onto the message; the single route below
    wraps this back into a 400 itself."""
    food_name = req.metadata.get("food_name")
    if not food_name:
        raise ValueError("metadata.food_name is required for event_type=food_log_created")
    return FoodLogEntryContract(
        date=req.occurred_at,
        meal=req.metadata.get("meal", "Snack"),
        food_name=food_name,
        source=req.source,
        source_id=req.source_id,
        category=req.category,
        serving_size=req.metadata.get("serving_size", 1.0),
        serving_unit=req.metadata.get("serving_unit", "serving"),
        calories=req.amount,
        nutrients=req.metadata.get("nutrients", {}),
    )


def _to_exercise_entry(req: EventLogRequest) -> ExerciseLogContract:
    """Pure parse, no I/O -- see _to_food_entry's own comment."""
    activity_name = req.metadata.get("activity_name")
    if not activity_name:
        raise ValueError("metadata.activity_name is required for event_type=exercise_log_created")
    return ExerciseLogContract(
        date=req.occurred_at,
        activity_name=activity_name,
        duration_minutes=req.metadata.get("duration_minutes"),
        calories_burned=req.amount,
        source=req.source or "manual",
        source_id=req.source_id,
        notes=req.metadata.get("notes"),
    )


async def _dispatch_log(user_id: int, req: EventLogRequest) -> dict:
    if req.event_type == "food_log_created":
        try:
            entry = _to_food_entry(req)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        food_log_id = await log_food_entry(user_id, entry)
        return {"id": food_log_id}

    if req.event_type == "exercise_log_created":
        try:
            entry = _to_exercise_entry(req)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        entry_id, _ = await log_exercise_entry(user_id, entry)
        return {"id": entry_id}

    raise HTTPException(
        status_code=400,
        detail=f"unknown event_type {req.event_type!r} — must be one of {sorted(CREATABLE_EVENT_TYPES)}",
    )


@router.post("/log", response_model=LogEventResponse)
async def log_event(req: EventLogRequest, user_id: int = Depends(get_current_user)):
    """Universal event ingestion. Dispatches to the same
    food_entry_contract.log_food_entry()/log_exercise_entry() functions
    the domain-specific endpoints (POST /food/log, POST /exercise) use —
    this adapter never writes SQL of its own, so a future change to
    storage/validation logic in those shared functions automatically
    applies here too. Those functions log their own domain_events row,
    so nothing further is needed here for the event to show up in
    GET /events."""
    result = await _dispatch_log(user_id, req)
    return {"status": "logged", **result}


# BATCH_EVENTS_SPEC.md's suggested default -- a plain constant, not
# configurable: large enough for a real bulk-log use case, small enough
# that a single request can't become a multi-minute transaction or an
# unbounded memory hold. (log_food_entries_bulk's OWN unbounded use from
# Cronometer sync is a separate, internal-only code path -- not reachable
# through this public endpoint, so it keeps no cap of its own.)
MAX_BATCH_SIZE = 200


class EventLogBatchRequest(BaseModel):
    events: list[EventLogRequest]


class BatchResultItem(BaseModel):
    status: str
    id: int


class EventLogBatchResponse(BaseModel):
    results: list[BatchResultItem]


@router.post("/log/batch", response_model=EventLogBatchResponse, status_code=201)
async def log_event_batch(req: EventLogBatchRequest, user_id: int = Depends(get_current_user)):
    """BATCH_EVENTS_SPEC.md: an arbitrary number of events in one all-or-
    nothing request. Every item is validated BEFORE the transaction opens
    -- one bad item 400s the whole batch, touching the database not at
    all. Dispatches to log_food_entries_bulk/log_exercise_entries_bulk
    (food_entry_contract.py) grouped by event_type, both sharing the ONE
    connection/transaction this route opens, so a food item and an
    exercise item in the same batch commit or roll back together even
    though they land in different tables. A duplicate (source,
    source_id) -- including one that duplicates an EARLIER item in this
    same batch -- is a per-item "duplicate" result, not a batch failure;
    see the in-batch dedup pass below for how that's caught even though
    the actual insert is one multi-row statement, not a per-item loop."""
    if not req.events:
        raise HTTPException(status_code=400, detail="events must contain at least 1 item")
    if len(req.events) > MAX_BATCH_SIZE:
        raise HTTPException(status_code=400, detail=f"events must contain at most {MAX_BATCH_SIZE} items")

    # Validate + parse every item before touching the database at all.
    parsed: list[tuple[int, str, object]] = []  # (original_index, event_type, FoodLogEntryContract|ExerciseLogContract)
    for i, item in enumerate(req.events):
        if item.event_type not in CREATABLE_EVENT_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"events[{i}]: unknown event_type {item.event_type!r} — must be one of {sorted(CREATABLE_EVENT_TYPES)}",
            )
        try:
            entry = _to_food_entry(item) if item.event_type == "food_log_created" else _to_exercise_entry(item)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"events[{i}]: {e}")
        parsed.append((i, item.event_type, entry))

    results: list[Optional[BatchResultItem]] = [None] * len(req.events)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            for event_type, table, bulk_fn in (
                ("food_log_created", "food_log", log_food_entries_bulk),
                ("exercise_log_created", "exercise_log", log_exercise_entries_bulk),
            ):
                group = [(i, entry) for i, et, entry in parsed if et == event_type]
                if not group:
                    continue

                # In-batch dedup FIRST: two items in the SAME batch sharing a
                # (source, source_id) must resolve to the SAME id, but the
                # bulk insert below is one multi-row statement with no
                # per-row existence check of its own -- so a later
                # duplicate is pulled out here and backfilled once the
                # first occurrence's real id is known (either freshly
                # inserted, or itself a duplicate of an already-persisted
                # row -- the chained case `in_batch_dup_of` resolves below).
                seen_at: dict[tuple[str, str], int] = {}
                in_batch_dup_of: dict[int, int] = {}
                deduped = []
                for i, entry in group:
                    if entry.source_id:
                        key = (entry.source, entry.source_id)
                        if key in seen_at:
                            in_batch_dup_of[i] = seen_at[key]
                            continue
                        seen_at[key] = i
                    deduped.append((i, entry))

                # THEN cross-request dedup, grouped by source (a batch's
                # items need not all share one) -- against rows already
                # committed before this request, via this transaction's
                # own `conn`.
                by_source: dict[str, list[tuple[int, object]]] = {}
                for i, entry in deduped:
                    if entry.source_id:
                        by_source.setdefault(entry.source, []).append((i, entry))
                existing: dict[int, int] = {}
                for source, items in by_source.items():
                    matches = await find_existing_by_source(conn, user_id, table, source, [entry.source_id for _, entry in items])
                    for i, entry in items:
                        if entry.source_id in matches:
                            existing[i] = matches[entry.source_id]

                to_insert = [(i, entry) for i, entry in deduped if i not in existing]
                for i, existing_id in existing.items():
                    results[i] = BatchResultItem(status="duplicate", id=existing_id)

                inserted_id_by_index: dict[int, int] = {}
                if to_insert:
                    new_ids = await bulk_fn(user_id, [entry for _, entry in to_insert], conn)
                    for (i, _), new_id in zip(to_insert, new_ids):
                        results[i] = BatchResultItem(status="logged", id=new_id)
                        inserted_id_by_index[i] = new_id

                for dup_i, original_i in in_batch_dup_of.items():
                    real_id = inserted_id_by_index.get(original_i, existing.get(original_i))
                    results[dup_i] = BatchResultItem(status="duplicate", id=real_id)

    return {"results": results}


def _row_to_event(r) -> dict:
    return {
        "id": r["id"],
        "user_id": r["user_id"],
        "event_type": r["event_type"],
        "category": r["category"],
        # occurred_at is the entity's own business date (e.g. food_log.date,
        # which a user can backdate) where one exists, else insert time --
        # see domain_events.py's log_domain_event docstring. created_at is
        # always the row's actual insert time (domain_events.logged_at),
        # so the two genuinely diverge for a backdated entry instead of
        # always collapsing to the same value.
        "occurred_at": r["occurred_at"].isoformat(),
        "created_at": r["logged_at"].isoformat(),
        "amount": r["amount"],
        "source": r["source"],
        "source_id": r["source_id"],
        "hidden": False,
        "status": None,
        "metadata": json.loads(r["metadata_json"]),
        "label": r["label"],
    }


@router.get("", response_model=GetEventsResponse)
async def get_events(
    start: str = Query(..., description="Inclusive start date, YYYY-MM-DD"),
    end: str = Query(..., description="Inclusive end date, YYYY-MM-DD"),
    event_type: Optional[str] = Query(None, description="Filter to one event_type; omit for all"),
    source: Optional[str] = Query(None, description="Filter to one source; omit for all"),
    user_id: int = Depends(get_current_user),
):
    """Universal event query across every domain_events row this
    tracker has logged. No pagination yet (next_page_token in the
    contract spec is unimplemented) — matches every other list endpoint
    in this app today, none of which paginate either; added if/when a
    real consumer needs it rather than speculatively."""
    events = await _query_events(user_id, start, end, event_type, source)
    return {"events": events, "total": len(events)}


async def _query_events(
    user_id: int, start: str, end: str,
    event_type: Optional[str] = None, source: Optional[str] = None,
) -> list[dict]:
    """Shared query logic behind GET /events and GET /aggregations/{type}
    — isolated here (rather than one route calling the other directly)
    so aggregation can reuse the exact same event set without going
    through FastAPI's dependency-injection machinery a second time.

    No fixed event_type whitelist to validate against here (unlike the
    old per-table-branching version) -- every event_type value lives in
    the same domain_events table now, so an unrecognized value is just
    a WHERE clause that matches nothing, not an error.

    occurred_at is timestamptz, not a bare date, so `end` (inclusive)
    is turned into an exclusive upper bound one day later rather than
    compared directly -- otherwise a row logged any time after midnight
    on the end date (which is nearly always, since only backdatable
    entities like food_log/exercise_log ever land on exact midnight)
    would be wrongly excluded."""
    # UTC midnights, explicitly -- a bare `date` handed to asyncpg gets
    # localized in the client process's timezone, which must match how
    # log_domain_event writes occurred_at (also pinned to UTC midnight).
    start_date = datetime.combine(date.fromisoformat(start), time.min, tzinfo=timezone.utc)
    end_exclusive = datetime.combine(date.fromisoformat(end) + timedelta(days=1), time.min, tzinfo=timezone.utc)
    conditions = ["user_id = $1", "occurred_at >= $2", "occurred_at < $3"]
    params: list = [user_id, start_date, end_exclusive]
    if event_type is not None:
        params.append(event_type)
        conditions.append(f"event_type = ${len(params)}")
    if source is not None:
        params.append(source)
        conditions.append(f"source = ${len(params)}")

    pool = await get_pool()
    async with pool.acquire() as conn:
        sql = select_clause("domain_events") + where_clause(conditions) + order_by_clause("occurred_at", "id")
        rows = await conn.fetch(sql, *params)
    return [_row_to_event(r) for r in rows]


@aggregations_router.get("/{agg_type}", response_model=AggregationsResponse)
async def get_aggregations(
    agg_type: str,
    start: str = Query(..., description="Inclusive start date, YYYY-MM-DD"),
    end: str = Query(..., description="Inclusive end date, YYYY-MM-DD"),
    user_id: int = Depends(get_current_user),
):
    """Sums `amount` grouped by the requested dimension, across every
    domain_events row in range regardless of event_type (created,
    updated, deleted all count) -- this can double-count the "same"
    real-world food/activity if, say, a pantry item's creation and its
    later consumption-triggered
    deletion both fall in the requested range and both carry a
    calorie-shaped amount. That's an intentional tradeoff of moving to a
    real CRUD log rather than a live-table snapshot: revisit if a real
    consumer needs "net" totals instead of "every logged action," which
    would mean filtering to just *_created (or just *_deleted) events at
    the call site rather than changing what this endpoint means."""
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
