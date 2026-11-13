"""Single shared table + read/write/delete helpers for "named sub-attributes
of a loggable item" — nutrients today, whatever comes next tomorrow. Any
tracked object that needs a flexible {name: {value, unit}} breakdown (a
food_log entry, a pantry item, a custom food, a recipe/meal item, and
whatever's added later) references the ONE nutrient_facts table (see
db.py) by (owner_type, owner_id) instead of getting its own hand-rolled
X_nutrients table plus its own duplicate read/write SQL.

This is the one place nutrient read/write/delete SQL lives for every
owner type -- adding a 6th "loggable item with named sub-attributes"
concept means one new OWNER_TYPES entry, never a new table or new
read/write SQL (see CLAUDE.md's "Named Sub-Attributes" pattern).

**Storage shape, changed 2026-11-12**: one row per owner (not one row per
nutrient). The original design gave every nutrient its own row --
food_log entries alone averaged 43.6 nutrient rows each, which had grown
to 4.6M total rows / 827MB (85% of this database) by the time it forced a
real decision: Neon's free-tier project storage cap. Collapsing to one
`nutrients` TEXT column (JSON-encoded `{name: {value, unit}}`, this
codebase's established convention for JSON-shaped columns -- see
db/__init__.py's own comment on domain_events.metadata_json, not a native
JSONB column) cut it to ~225MB, measured against the real production data
before migrating, not guessed. Every function below keeps its exact prior
signature and return shape, so no caller (food_entry_contract.py,
routers/custom_foods.py, routers/meals.py, routers/pantry.py,
routers/recipes.py) needed to change -- only this module's internals.

owner_type is a plain string discriminator, not a foreign key — Postgres
has no polymorphic FK across 5 different parent tables in one column.
Referential integrity for this table is therefore enforced in application
code, not the schema: every DELETE of an owning row (or a bulk replace of
its items, e.g. re-saving a whole recipe) MUST also call
delete_nutrient_facts()/delete_nutrient_facts_bulk() for it, or its
nutrient_facts rows become permanently orphaned (harmless to correctness
— nothing reads a row for an owner_id that no longer exists anywhere —
but a real, avoidable storage leak).
"""
import json
from typing import Iterable

# owner_type -> the table it references. Documentation only (no FK is
# possible across 5 different parent tables in one column) — kept here so
# adding a new owner_type is a one-line addition, not a new table.
OWNER_TYPES = {
    "food_log": "food_log",
    "pantry_item": "pantry_items",
    "custom_food": "custom_foods",
    "recipe_item": "recipe_items",
    "meal_item": "meal_items",
}


def _normalize_nutrients(nutrients: dict) -> dict:
    """Normalize a {name: {value, unit}} dict, silently dropping any entry
    missing a numeric value — the ONE normalization implementation;
    callers (food_entry_contract.py, routers/custom_foods.py,
    routers/meals.py, routers/recipes.py) use this rather than
    reimplementing it."""
    normalized = {}
    for name, info in (nutrients or {}).items():
        if not isinstance(info, dict):
            continue
        value = info.get("value")
        if value is None:
            continue
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        normalized[name] = {"value": value, "unit": info.get("unit", "")}
    return normalized


# Shallow-merges the new nutrients blob into whatever's already stored for
# this owner (nutrient_facts.nutrients::jsonb || EXCLUDED...), matching the
# old per-row upsert semantics exactly: a nutrient name present in both
# keeps the new value, every other existing nutrient for this owner is
# left untouched. Cast back to ::text since this codebase stores
# JSON-shaped columns as TEXT, not native JSONB (see module docstring).
_UPSERT_SQL = """INSERT INTO nutrient_facts (owner_type, owner_id, nutrients)
       VALUES ($1, $2, $3)
       ON CONFLICT (owner_type, owner_id)
       DO UPDATE SET nutrients = (nutrient_facts.nutrients::jsonb || EXCLUDED.nutrients::jsonb)::text"""


async def write_nutrients(conn, owner_type: str, owner_id: int, nutrients: dict) -> None:
    """Insert (or update, on conflict) one owner's full nutrient
    breakdown. Does NOT clear existing rows first — a caller REPLACING an
    item's nutrients (an edit) should call delete_nutrient_facts() first;
    a caller creating a brand-new item never needs to."""
    normalized = _normalize_nutrients(nutrients)
    if not normalized:
        return
    await conn.execute(_UPSERT_SQL, owner_type, owner_id, json.dumps(normalized))


async def write_nutrients_bulk(conn, owner_type: str, owner_nutrients: list[tuple[int, dict]]) -> None:
    """Bulk equivalent of write_nutrients() for a caller writing many
    owners' worth of nutrients in one go (e.g. an importer processing
    hundreds of entries) -- one executemany for every owner's row at
    once, instead of one execute per owner. Same insert-or-update
    semantics as write_nutrients(); just batched. `owner_nutrients` is a
    list of (owner_id, nutrients_dict) pairs."""
    rows = []
    for owner_id, nutrients in owner_nutrients:
        normalized = _normalize_nutrients(nutrients)
        if normalized:
            rows.append((owner_type, owner_id, json.dumps(normalized)))
    if not rows:
        return
    await conn.executemany(_UPSERT_SQL, rows)


async def read_nutrients_bulk(conn, owner_type: str, owner_ids: Iterable[int]) -> dict[int, dict]:
    """Read every owner's nutrients in one query. Returns
    {owner_id: {name: {value, unit}}}, each already in TrackStack's
    canonical display order (see nutrient_groups.order_nutrients) — every
    caller of this function gets ordered nutrients for free, rather than
    needing to remember to call order_nutrients() itself."""
    from .nutrient_groups import order_nutrients

    owner_ids = list(owner_ids)
    if not owner_ids:
        return {}
    rows = await conn.fetch(
        "SELECT owner_id, nutrients FROM nutrient_facts "
        "WHERE owner_type = $1 AND owner_id = ANY($2::int[])",
        owner_type, owner_ids,
    )
    by_owner: dict[int, dict] = {
        r["owner_id"]: (json.loads(r["nutrients"]) if r["nutrients"] else {}) for r in rows
    }
    return {owner_id: order_nutrients(nutrients) for owner_id, nutrients in by_owner.items()}


async def read_nutrients(conn, owner_type: str, owner_id: int) -> dict:
    """Read one owner's nutrients. Thin wrapper over read_nutrients_bulk
    for the common single-id case."""
    return (await read_nutrients_bulk(conn, owner_type, [owner_id])).get(owner_id, {})


async def delete_nutrient_facts(conn, owner_type: str, owner_id: int) -> None:
    """Delete one owner's nutrient rows. Call this explicitly wherever the
    owning row itself is deleted, or its items are being replaced —
    nutrient_facts has no FK to cascade automatically (see module
    docstring)."""
    await conn.execute(
        "DELETE FROM nutrient_facts WHERE owner_type = $1 AND owner_id = $2",
        owner_type, owner_id,
    )


async def delete_nutrient_facts_bulk(conn, owner_type: str, owner_ids: Iterable[int]) -> None:
    """Bulk version of delete_nutrient_facts, for clearing every item
    under a parent being deleted/replaced in one query (e.g. every
    recipe_item under a recipe, before re-inserting the edited list)."""
    owner_ids = list(owner_ids)
    if not owner_ids:
        return
    await conn.execute(
        "DELETE FROM nutrient_facts WHERE owner_type = $1 AND owner_id = ANY($2::int[])",
        owner_type, owner_ids,
    )
