"""Storage for nutrient/macro targets, which live in the `goals` table
since the 2026-09-24 Targets/Goals unification (previously separate
nutrition_targets/macro_target_settings tables -- see db/__init__.py's
migration and app/nutrition_targets.py for the conventions):

  source = 'dri_default'  auto-seeded DRI nutrient target (floor = a gte
                          goal, ceiling = an lte goal, both daily)
  source = 'user_target'  a nutrient target the user customized
  source = 'macro_target' the calorie/protein/carbs/fat rows written by
                          PUT /targets/macros

A nutrient's goals are identified by measure_query's `measureField`
("nutrient:<Name>") plus comparator; measure_query is JSON-encoded TEXT
(this codebase's convention), hence the ::jsonb casts."""
import json

from .. import get_pool
from ...goal_signature import goal_signature, signature_of_goal

NUTRIENT_TARGET_SOURCES = ("dri_default", "user_target")


async def list_nutrient_target_goals(user_id: int):
    """One row per nutrient floor/ceiling goal (a nutrient with both
    yields two rows). Includes a macro_target goal when it's a plain
    floor/ceiling on a nutrient (Protein's is) -- there is only ever ONE
    protein floor, and it's that goal, not a second DRI copy beside it."""
    pool = await get_pool()
    return await pool.fetch(
        """SELECT id, comparator, reference_amount, source,
                  substr(measure_query::jsonb ->> 'measureField', 10) AS nutrient_name
           FROM goals
           WHERE user_id = $1
             AND measure_query::jsonb ->> 'measureField' IS NOT NULL
             AND (source = ANY($2::text[]) OR (source = 'macro_target' AND comparator IN ('gte', 'lte')))""",
        user_id, list(NUTRIENT_TARGET_SOURCES),
    )


async def apply_nutrient_goal_changes(user_id: int, inserts: list, updates: list, deletes: list) -> None:
    """inserts: (label, comparator, measure_query_dict, amount, source);
    updates: (goal_id, amount, source); deletes: goal ids. One transaction,
    so a failed reseed can't leave a nutrient with half its targets."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            if deletes:
                await conn.execute("DELETE FROM goals WHERE user_id = $1 AND id = ANY($2::int[])", user_id, deletes)
            if updates:
                await conn.executemany(
                    "UPDATE goals SET reference_amount = $2, source = $3, updated_at = now() WHERE id = $1 AND user_id = $4",
                    [(gid, amount, source, user_id) for gid, amount, source in updates],
                )
            if inserts:
                await conn.executemany(
                    """INSERT INTO goals (user_id, label, severity, comparator, measure_query, reference_amount, notify_on_crossing, source)
                       VALUES ($1, $2, 'target', $3, $4, $5, FALSE, $6)""",
                    [(user_id, label, comparator, json.dumps(mq), amount, source) for label, comparator, mq, amount, source in inserts],
                )


async def get_macro_goal_amounts(user_id: int) -> dict:
    """{label: reference_amount} for this user's macro_target goals."""
    pool = await get_pool()
    rows = await pool.fetch("SELECT label, reference_amount FROM goals WHERE user_id = $1 AND source = 'macro_target'", user_id)
    return {r["label"]: r["reference_amount"] for r in rows}


async def set_macro_goals(user_id: int, definitions: list) -> None:
    """definitions: (label, comparator, tolerance_percent, measure_query_dict,
    amount) -- updates the user's existing macro_target goal for that label,
    or takes over an existing goal that already asserts the same thing
    (Protein's daily floor is also a DRI-preset goal -- adopting it keeps
    the user at one protein floor rather than two), or inserts a new one."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = [_decode(r) for r in await conn.fetch("SELECT * FROM goals WHERE user_id = $1 AND is_active = TRUE", user_id)]
            existing = {r["label"]: r["id"] for r in rows if r["source"] == "macro_target"}
            by_signature = {}
            for r in rows:
                by_signature.setdefault(signature_of_goal(r), r)
            for label, comparator, tolerance, mq, amount in definitions:
                if label in existing:
                    await conn.execute(
                        "UPDATE goals SET reference_amount = $2, is_active = TRUE, updated_at = now() WHERE id = $1",
                        existing[label], amount,
                    )
                    continue
                twin = by_signature.get(goal_signature(mq, None, comparator, "target"))
                if twin is not None:
                    await conn.execute(
                        """UPDATE goals SET label = $2, source = 'macro_target', reference_amount = $3,
                                            tolerance_percent = $4, updated_at = now() WHERE id = $1""",
                        twin["id"], label, amount, tolerance,
                    )
                else:
                    await conn.execute(
                        """INSERT INTO goals (user_id, label, severity, comparator, tolerance_percent, measure_query, reference_amount, notify_on_crossing, source)
                           VALUES ($1, $2, 'target', $3, $4, $5, $6, FALSE, 'macro_target')""",
                        user_id, label, comparator, tolerance, json.dumps(mq), amount,
                    )


def _decode(row) -> dict:
    d = dict(row)
    d["measure_query"] = json.loads(d["measure_query"])
    d["reference_query"] = json.loads(d["reference_query"]) if d["reference_query"] is not None else None
    return d


async def restore_preset_goal(goal_id: int, user_id: int, amount: float, source: str) -> None:
    """Puts a preset goal back to a default amount (and, for DRI presets,
    back to the 'dri_default' source so a later profile change refreshes
    it again)."""
    pool = await get_pool()
    await pool.execute(
        "UPDATE goals SET reference_amount = $3, source = $4, is_active = TRUE, updated_at = now() WHERE id = $1 AND user_id = $2",
        goal_id, user_id, amount, source,
    )
