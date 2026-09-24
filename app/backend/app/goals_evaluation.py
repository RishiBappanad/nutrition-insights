"""
Goal evaluation -- per workspace-notes/RECURRING_AND_GOALS_SPEC.md's
"Goals" section, a goal compares a `measure` GoalQuery against a
`reference` (a constant or itself a GoalQuery) via `comparator`. This
module owns comparator logic and the event-triggered before/after
transition check -- the actual query evaluation lives in goal_query.py
(the reusable interpreter), ported from finance-tracker's
goals-evaluation.ts.

No persisted evaluation state: the `goals` table is pure definition --
both sides of a transition check are computed live, every time, by
re-running the measure/reference queries with and without the just-
inserted event.

No inflation adjustment: finance-tracker's copy of this module adjusts a
computed reference value for CPI inflation when comparing money across
two time windows -- meaningless for nutrition (calories/grams don't
inflate), so `inflation_adjusted` exists on the table only for schema
parity across trackers (Tenet #1's "same shape, no shared table" applies
to the SCHEMA, not to every column necessarily being load-bearing in
every tracker) and is always a no-op here. Flagged explicitly rather
than silently dropped, since a client could still set it to true without
this file rejecting the write -- see routers/goals.py's own note.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

from .goal_query import GoalQuery, parse_goal_query, evaluate_goal_query
from .domain_events import log_domain_event

COMPARATORS = ("lte", "gte", "eq", "within_tolerance_percent")
SEVERITIES = ("warning", "target")


def is_valid_comparator(value) -> bool:
    return isinstance(value, str) and value in COMPARATORS


def is_valid_severity(value) -> bool:
    return isinstance(value, str) and value in SEVERITIES


def is_compliant(comparator: str, measure: float, reference: float, tolerance_percent: Optional[float] = None) -> bool:
    if comparator == "lte":
        return measure <= reference
    if comparator == "gte":
        return measure >= reference
    if comparator == "eq":
        return measure == reference
    # within_tolerance_percent -- a symmetric +/-tolerance_percent% band
    # around `reference`.
    delta = reference * (tolerance_percent / 100)
    return reference - delta <= measure <= reference + delta


def percent_of_reference(measure: float, reference: float) -> float:
    if reference == 0:
        return 0.0 if measure == 0 else 100.0
    return round((measure / reference) * 10000) / 100


@dataclass
class EvaluatedGoal:
    measure_value: Optional[float]  # None = no readings yet (a "last" measure over an empty window)
    reference_value: float
    comparator: str
    tolerance_percent: Optional[float]
    percent: float
    is_compliant: bool
    severity: str

    @property
    def has_data(self) -> bool:
        return self.measure_value is not None


def _parse_stored_query(raw, label: str) -> GoalQuery:
    """`raw` is whatever the `goals` table's measure_query/reference_query
    column actually holds -- a JSON-encoded TEXT string (this codebase's
    established convention for every JSON-shaped column, e.g.
    domain_events.metadata_json, food_log.nutrients_json; there is no
    native JSONB column type in use anywhere here), or already a dict if
    a caller parsed it upstream (e.g. a fresh POST /goals body). Decoding
    here means every caller can just pass the raw column value through
    without remembering to json.loads() it first."""
    value = json.loads(raw) if isinstance(raw, str) else raw
    parsed = parse_goal_query(value)
    if isinstance(parsed, str):
        raise ValueError(f"Stored {label} on goal is invalid: {parsed}")
    return parsed


async def compute_goal_status(conn, goal: dict, exclude_event_id: Optional[int] = None) -> EvaluatedGoal:
    """Evaluates one goal's current measure_value/reference_value/
    compliance, excluding `exclude_event_id` from both queries when given
    -- the "before" half of the event-triggered transition check (see
    evaluate_goal_transition) passes the just-inserted event's id here to
    compute what the result would have been without it; a live status
    read (routers/goals.py) omits it. Pure -- no writes; the only writes
    Goals ever produces are the goal_met/goal_exceeded domain_events
    rows, written by evaluate_goal_transition, never by this function."""
    measure_query = _parse_stored_query(goal["measure_query"], "measure_query")
    measure = await evaluate_goal_query(conn, goal["user_id"], measure_query, exclude_event_id=exclude_event_id)

    if goal["reference_query"] is not None:
        reference_query = _parse_stored_query(goal["reference_query"], "reference_query")
        reference = await evaluate_goal_query(conn, goal["user_id"], reference_query, exclude_event_id=exclude_event_id)
        reference_value = reference.value
    else:
        reference_value = goal["reference_amount"]

    comparator = goal["comparator"]
    if measure.value is None or reference_value is None:
        # No readings at all: not compliant (there's nothing to be
        # compliant with), and no percent to show.
        return EvaluatedGoal(
            measure_value=None, reference_value=round(reference_value or 0, 2), comparator=comparator,
            tolerance_percent=goal["tolerance_percent"], percent=0.0, is_compliant=False, severity=goal["severity"],
        )
    return EvaluatedGoal(
        measure_value=measure.value,
        reference_value=round(reference_value, 2),
        comparator=comparator,
        tolerance_percent=goal["tolerance_percent"],
        percent=percent_of_reference(measure.value, reference_value),
        is_compliant=is_compliant(comparator, measure.value, reference_value, goal["tolerance_percent"]),
        severity=goal["severity"],
    )


async def evaluate_goal_transition(conn, goal: dict, triggering_event_id: int) -> None:
    """The event-triggered path: computes compliance twice -- "after" (the
    normal compute_goal_status, including the just-inserted event) and
    "before" (the same computation with that event excluded) -- and logs
    a transition only when they differ. No goals row is ever written
    here; both sides are computed fresh from domain_events' own history
    each time, so there's no cache to go stale."""
    before = await compute_goal_status(conn, goal, exclude_event_id=triggering_event_id)
    after = await compute_goal_status(conn, goal)

    if before.is_compliant == after.is_compliant:
        return

    await log_domain_event(
        conn,
        goal["user_id"],
        "goal",
        goal["id"],
        "met" if after.is_compliant else "exceeded",
        category=None,
        amount=0,
        label=goal["label"] or f"Goal #{goal['id']}",
        metadata={
            "severity": goal["severity"],
            "comparator": after.comparator,
            "measure_value": after.measure_value,
            "reference_value": after.reference_value,
        },
    )


async def evaluate_goals_for_event(conn, user_id: int, event_id: int, category: Optional[str], event_type: str, owner_type: str, action: str) -> None:
    """Only goals with notify_on_crossing set are evaluated for transitions
    -- the crossing events exist to be notified on, and skipping the rest
    matters for cost: DRI-seeded nutrient targets are ~48 goals per user,
    each needing two aggregate queries per triggering event, all for
    goal_met/goal_exceeded rows nobody asked to hear about. (Live status
    reads, GET /goals/:id/status and the dashboard's progress, don't go
    through this path and are unaffected.)

    Called from domain_events.py's log_domain_event, right after every
    event is written (same open `conn`/transaction) -- narrows to this
    user's active goals, then to those whose measure_query's cheap eq/in
    filters could plausibly match this event (could_match_event,
    goal_query.py) before running the (comparatively expensive, two-
    query) transition check on each survivor."""
    from .goal_query import could_match_event

    rows = await conn.fetch(
        "SELECT * FROM goals WHERE user_id = $1 AND is_active = TRUE AND notify_on_crossing = TRUE",
        user_id,
    )
    for row in rows:
        goal = dict(row)
        measure_query = _parse_stored_query(goal["measure_query"], "measure_query")
        if not could_match_event(measure_query, category, event_type, owner_type, action):
            continue
        await evaluate_goal_transition(conn, goal, event_id)
