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
    pool = await get_pool()
    row = await pool.fetchrow(
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
    """`include_system` adds the auto-managed nutrient targets (source
    'dri_default'/'user_target', ~48 per user) -- omitted by default so the
    list a person actually browses stays their own goals plus their macro
    targets, not two pages of RDA rows (those have their own editor)."""
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
    measure_query/reference_query, if present, must already be JSON-
    encoded dicts (this function does the json.dumps). Every entry is a
    COALESCE-guarded partial update (a value of `None` for a nullable
    column intentionally CLEARS it -- e.g. reference_query going back to
    None when a goal switches from computed-baseline to fixed-amount --
    so callers must only include keys they actually want written, never
    the full column set with unset fields defaulted to None."""
    if not updates:
        return await get_owned_goal(goal_id, user_id)

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

    sql = f"UPDATE goals SET {', '.join(set_parts)}, updated_at = now() WHERE id = ${id_idx} AND user_id = ${user_idx} RETURNING *"
    pool = await get_pool()
    row = await pool.fetchrow(sql, *params)
    return _row_to_goal(row) if row else None


async def delete_goal(goal_id: int, user_id: int) -> bool:
    pool = await get_pool()
    result = await pool.execute("DELETE FROM goals WHERE id = $1 AND user_id = $2", goal_id, user_id)
    return result == "DELETE 1"
