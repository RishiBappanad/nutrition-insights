"""Named query module for the `goals` table -- see CLAUDE.md's "Named
Query Modules, Not Inline SQL in Routers" pattern. measure_query/
reference_query are stored as JSON-encoded TEXT (this codebase's
established convention for every JSON-shaped column; there's no native
JSONB column type in use anywhere here), so every function here takes/
returns them already dict-decoded -- callers never touch json.dumps/
loads for these two columns directly.
"""
import json
from typing import Optional

from .. import get_pool
from ..sql_builder import validate_identifier
from ...goal_signature import signature_of_goal

# Serializes goal writes per user, so "check for a duplicate, then insert"
# can't race a second identical request into a second row (a double-click,
# a retried request). Held only for the transaction.
_GOAL_WRITE_LOCK = 872003


class DuplicateGoalError(Exception):
    """A write would leave the user with two active goals asserting the
    same thing about the same measure (see goal_signature.py)."""

    def __init__(self, existing: dict):
        super().__init__(f"duplicate of goal {existing['id']}")
        self.existing = existing


async def _lock_user_goals(conn, user_id: int) -> None:
    await conn.execute("SELECT pg_advisory_xact_lock($1, $2)", _GOAL_WRITE_LOCK, user_id)


async def _find_duplicate(conn, user_id: int, candidate: dict, exclude_id: Optional[int] = None) -> Optional[dict]:
    """The user's other ACTIVE goal with the candidate's signature, if any.
    Compared in Python over the user's goals (tens to ~100 rows) rather
    than in SQL -- the signature is a canonicalization of JSON-in-TEXT
    columns, which is far clearer as code than as a query."""
    target = signature_of_goal(candidate)
    rows = await conn.fetch("SELECT * FROM goals WHERE user_id = $1 AND is_active = TRUE", user_id)
    for row in rows:
        if exclude_id is not None and row["id"] == exclude_id:
            continue
        goal = _row_to_goal(row)
        if signature_of_goal(goal) == target:
            return goal
    return None


def _row_to_goal(row) -> dict:
    d = dict(row)
    d["measure_query"] = json.loads(d["measure_query"])
    d["reference_query"] = json.loads(d["reference_query"]) if d["reference_query"] is not None else None
    return d


async def create_goal(
    user_id: int,
    label: Optional[str],
    severity: str,
    comparator: str,
    tolerance_percent: Optional[float],
    measure_query: dict,
    reference_amount: Optional[float],
    reference_query: Optional[dict],
    inflation_adjusted: bool,
    notify_on_crossing: bool,
) -> dict:
    """Raises DuplicateGoalError instead of inserting a second goal that
    asserts the same thing as an existing active one."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _lock_user_goals(conn, user_id)
            duplicate = await _find_duplicate(conn, user_id, {
                "measure_query": measure_query, "reference_query": reference_query,
                "comparator": comparator, "severity": severity,
            })
            if duplicate:
                raise DuplicateGoalError(duplicate)
            row = await conn.fetchrow(
                """INSERT INTO goals
                       (user_id, label, severity, comparator, tolerance_percent, measure_query,
                        reference_amount, reference_query, inflation_adjusted, notify_on_crossing)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                   RETURNING *""",
                user_id, label, severity, comparator, tolerance_percent, json.dumps(measure_query),
                reference_amount, json.dumps(reference_query) if reference_query is not None else None,
                inflation_adjusted, notify_on_crossing,
            )
    return _row_to_goal(row)


async def list_goals(user_id: int, active_only: bool, include_system: bool = False) -> list[dict]:
    """`include_system` adds the preset nutrient targets (source
    'dri_default'/'user_target', ~48 per user) -- omitted by default so a
    plain API listing stays the user's own goals plus their macro targets;
    the Targets & Goals page asks for them explicitly."""
    conditions = ["user_id = $1"]
    if active_only:
        conditions.append("is_active = TRUE")
    if not include_system:
        conditions.append("(source IS NULL OR source = 'macro_target')")
    rows = await (await get_pool()).fetch(f"SELECT * FROM goals WHERE {' AND '.join(conditions)} ORDER BY created_at, id", user_id)
    return [_row_to_goal(r) for r in rows]


async def get_owned_goal(goal_id: int, user_id: int) -> Optional[dict]:
    pool = await get_pool()
    row = await pool.fetchrow("SELECT * FROM goals WHERE id = $1 AND user_id = $2", goal_id, user_id)
    return _row_to_goal(row) if row else None


async def update_goal(goal_id: int, user_id: int, updates: dict) -> Optional[dict]:
    """`updates` maps already-validated column names to new values --
    measure_query/reference_query, if present, must already be plain dicts
    (this function does the json.dumps). A value of `None` for a nullable
    column intentionally CLEARS it -- e.g. reference_query going back to
    None when a goal switches from computed-baseline to fixed-amount --
    so callers must only include keys they actually want written, never
    the full column set with unset fields defaulted to None.

    Raises DuplicateGoalError if the goal, as edited, would duplicate
    another active goal (checked against the edited values, so changing a
    goal's amount alone never trips it, but flipping it to a comparator or
    measure another goal already covers does)."""
    if not updates:
        return await get_owned_goal(goal_id, user_id)

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _lock_user_goals(conn, user_id)
            current_row = await conn.fetchrow("SELECT * FROM goals WHERE id = $1 AND user_id = $2 FOR UPDATE", goal_id, user_id)
            if current_row is None:
                return None
            effective = {**_row_to_goal(current_row), **updates}
            if effective["is_active"]:
                duplicate = await _find_duplicate(conn, user_id, effective, exclude_id=goal_id)
                if duplicate:
                    raise DuplicateGoalError(duplicate)

            set_parts = []
            params: list = []
            for column, value in updates.items():
                validate_identifier(column)
                if column in ("measure_query", "reference_query") and value is not None:
                    value = json.dumps(value)
                params.append(value)
                set_parts.append(f"{column} = ${len(params)}")
            params.append(goal_id)
            id_idx = len(params)
            params.append(user_id)
            user_idx = len(params)
            row = await conn.fetchrow(
                f"UPDATE goals SET {', '.join(set_parts)}, updated_at = now() WHERE id = ${id_idx} AND user_id = ${user_idx} RETURNING *",
                *params,
            )
    return _row_to_goal(row) if row else None


async def list_active_goals_for_signatures(user_id: int) -> list[dict]:
    """Every active goal, any source -- what preset/seed logic compares a
    candidate against to avoid creating a duplicate."""
    rows = await (await get_pool()).fetch("SELECT * FROM goals WHERE user_id = $1 AND is_active = TRUE", user_id)
    return [_row_to_goal(r) for r in rows]


async def delete_goal(goal_id: int, user_id: int) -> bool:
    pool = await get_pool()
    result = await pool.execute("DELETE FROM goals WHERE id = $1 AND user_id = $2", goal_id, user_id)
    return result == "DELETE 1"
